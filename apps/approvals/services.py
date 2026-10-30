"""Generic approval workflow engine.

All state changes run inside a transaction with the request row locked
(SELECT … FOR UPDATE), so two simultaneous "Approve" clicks cannot both pass
the status check — the second sees the new status and is refused. Side
effects (ledger debit, attendance adjustment) run in the same transaction and
are additionally idempotent at the database level.
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.permissions import is_admin, is_hr, own_employee
from apps.policies.services import approval_chain

from .models import OPEN_STATUSES, PENDING_STATUSES, STEP_STATUS, ApprovalAction, Status


class WorkflowError(ValidationError):
    pass


def _record(req, actor, action, from_status, comment="", step=""):
    return ApprovalAction.objects.create(
        content_type=ContentType.objects.get_for_model(req.__class__),
        object_id=req.pk,
        request_type=req.workflow_type(),
        employee=req.employee,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        from_status=from_status,
        to_status=req.status,
        step=step,
        comment=comment or "",
    )


def _manager_has_login(manager):
    return bool(manager and manager.is_active and hasattr(manager, "user") and manager.user.is_active)


def _advance_to(req, index):
    """Move to the first actionable step at or after `index`. Returns True if a step was found."""
    chain = req.chain
    while index < len(chain):
        step = chain[index]
        if step == "MANAGER" and not _manager_has_login(req.assigned_manager):
            index += 1  # no reporting manager with a login → skip to next step
            continue
        req.current_step = index
        req.status = STEP_STATUS[step]
        return True
    return False


def _notify(req, event, actor, comment=""):
    from apps.notifications.services import notify_request_event

    transaction.on_commit(lambda: notify_request_event(req.__class__, req.pk, event, actor.pk if actor else None, comment))


@transaction.atomic
def submit(req, actor):
    """Persist a new request and start its approval chain."""
    employee = req.employee
    req.chain = approval_chain(req.workflow_type())
    req.assigned_manager = employee.reporting_manager
    req.submitted_by = actor
    req.submitted_at = timezone.now()
    if not _advance_to(req, 0):
        # Chain contained only a MANAGER step and there is no manager: route to HR.
        req.chain = req.chain + ["HR"]
        _advance_to(req, len(req.chain) - 1)
    req.save()
    _record(req, actor, ApprovalAction.Action.SUBMIT, "", step=req.current_step_name)
    audit.log(actor, f"{req.workflow_type().lower()}.submitted", req)
    _notify(req, "submitted", actor)
    return req


def can_act(user, req):
    """May `user` approve / reject / request info on `req` right now?"""
    if not user.is_authenticated or req.status not in PENDING_STATUSES:
        return False
    me = own_employee(user)
    if me and me.pk == req.employee_id:
        return False  # never approve your own request
    step = req.current_step_name
    if step == "MANAGER":
        return bool(me and req.assigned_manager_id == me.pk) or is_admin(user)
    if step == "HR":
        return is_hr(user)
    if step == "ADMIN":
        return is_admin(user)
    return False


def can_view_request(user, req):
    from apps.core.permissions import employee_scope

    if is_hr(user):
        return True
    me = own_employee(user)
    if not me:
        return False
    if req.employee_id == me.pk or req.assigned_manager_id == me.pk:
        return True
    return employee_scope(user).filter(pk=req.employee_id).exists()


def _locked(req):
    return req.__class__.objects.select_for_update().get(pk=req.pk)


@transaction.atomic
def decide(req, actor, action, comment=""):
    """Manager / HR / Admin decision: APPROVE, REJECT or REQUEST_INFO."""
    req = _locked(req)
    if not can_act(actor, req):
        raise WorkflowError("You are not authorised to act on this request in its current state.")
    comment = (comment or "").strip()
    from_status = req.status
    step = req.current_step_name
    now = timezone.now()

    if action == "APPROVE":
        if _advance_to(req, req.current_step + 1):
            req.save()
            _record(req, actor, ApprovalAction.Action.FORWARD, from_status, comment, step)
            audit.log(actor, f"{req.workflow_type().lower()}.forwarded", req, reason=comment)
            _notify(req, "forwarded", actor, comment)
            return req
        req.status = Status.APPROVED
        req.decided_at, req.decided_by, req.decision_comment = now, actor, comment
        req.save()
        req.on_approved(actor)
        _record(req, actor, ApprovalAction.Action.APPROVE, from_status, comment, step)
        audit.log(actor, f"{req.workflow_type().lower()}.approved", req, reason=comment)
        _notify(req, "approved", actor, comment)
    elif action == "REJECT":
        if not comment:
            raise WorkflowError("A reason is required to reject a request.")
        req.status = Status.REJECTED
        req.decided_at, req.decided_by, req.decision_comment = now, actor, comment
        req.save()
        req.on_rejected(actor)
        _record(req, actor, ApprovalAction.Action.REJECT, from_status, comment, step)
        audit.log(actor, f"{req.workflow_type().lower()}.rejected", req, reason=comment)
        _notify(req, "rejected", actor, comment)
    elif action == "REQUEST_INFO":
        if not comment:
            raise WorkflowError("Please describe what information is needed.")
        req.info_return_status = req.status
        req.status = Status.NEEDS_INFORMATION
        req.save()
        _record(req, actor, ApprovalAction.Action.REQUEST_INFO, from_status, comment, step)
        audit.log(actor, f"{req.workflow_type().lower()}.info_requested", req, reason=comment)
        _notify(req, "info_requested", actor, comment)
    else:
        raise WorkflowError("Unknown action.")
    return req


@transaction.atomic
def respond(req, actor, comment):
    """Employee answers a clarification request; the request returns to its approver."""
    req = _locked(req)
    me = own_employee(actor)
    if not me or me.pk != req.employee_id:
        raise WorkflowError("Only the requesting employee can respond.")
    if req.status != Status.NEEDS_INFORMATION:
        raise WorkflowError("This request is not awaiting your clarification.")
    if not (comment or "").strip():
        raise WorkflowError("Please enter your response.")
    from_status = req.status
    req.status = req.info_return_status or STEP_STATUS.get(req.current_step_name, Status.PENDING_MANAGER)
    req.info_return_status = ""
    req.save()
    _record(req, actor, ApprovalAction.Action.RESPOND, from_status, comment)
    _notify(req, "responded", actor, comment)
    return req


@transaction.atomic
def cancel(req, actor, reason=""):
    """Employee cancels an open request; HR/Admin may cancel an approved one (with reversal)."""
    req = _locked(req)
    me = own_employee(actor)
    from_status = req.status
    reason = (reason or "").strip()
    if req.status in OPEN_STATUSES and me and me.pk == req.employee_id:
        req.status = Status.CANCELLED
        req.save()
    elif req.status in OPEN_STATUSES and is_hr(actor):
        if not reason:
            raise WorkflowError("A reason is required.")
        req.status = Status.CANCELLED
        req.save()
    elif req.status == Status.APPROVED and is_hr(actor) and not (me and me.pk == req.employee_id):
        if not reason:
            raise WorkflowError("A reason is required to cancel an approved request.")
        req.status = Status.CANCELLED
        req.save()
        req.on_cancel_approved(actor, reason)
    else:
        raise WorkflowError("This request cannot be cancelled by you in its current state.")
    _record(req, actor, ApprovalAction.Action.CANCEL, from_status, reason)
    audit.log(actor, f"{req.workflow_type().lower()}.cancelled", req, reason=reason)
    _notify(req, "cancelled", actor, reason)
    return req


def add_remark(req, actor, comment):
    if not (can_act(actor, req) or is_hr(actor)):
        raise WorkflowError("You cannot add remarks to this request.")
    if not (comment or "").strip():
        raise WorkflowError("Remark cannot be empty.")
    return _record(req, actor, ApprovalAction.Action.REMARK, req.status, comment)
