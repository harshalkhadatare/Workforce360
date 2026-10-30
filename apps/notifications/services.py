"""Notification and email delivery service.

* Every email gets an EmailDeliveryLog row with a unique dedupe key, so a
  retried transaction or a repeated call cannot send the same event twice.
* Status SENT means "accepted by the configured mail server" — the portal
  never claims final delivery to the inbox.
* With email disabled / not configured, rows are recorded as NOT_CONFIGURED.
* Secrets (SMTP password, reset tokens) are never written to the log.
"""
import logging
import smtplib
import socket
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import EmailDeliveryLog, Notification, NotificationSettings, RecipientGroup

log = logging.getLogger(__name__)
TRANSIENT_ERRORS = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, ConnectionError, TimeoutError)


def portal_url(path):
    return f"{settings.PORTAL_BASE_URL}{path}"


def email_configured():
    return bool(settings.EMAIL_ENABLED) and NotificationSettings.load().email_notifications_enabled


# ---------------------------------------------------------------------------
# Low-level
# ---------------------------------------------------------------------------
def notify_in_app(user, event_type, title, message="", link="", dedupe_key=None):
    if user is None or not user.is_active:
        return None
    key = (dedupe_key or f"{event_type}:{timezone.now().timestamp()}")[:180] + f":u{user.pk}"
    try:
        with transaction.atomic():
            return Notification.objects.create(user=user, event_type=event_type, title=title[:200], message=message,
                                               link=link, dedupe_key=key)
    except IntegrityError:
        return None


def queue_email(*, event_type, recipient, subject, context, dedupe_key, related=None, cc=None, template="emails/notification",
                role=EmailDeliveryLog.Role.TO):
    if not recipient:
        return None
    full_ctx = {
        "company_name": settings.COMPANY_NAME,
        "helpdesk_email": NotificationSettings.load().helpdesk_email,
        "helpdesk_phone": NotificationSettings.load().helpdesk_phone,
        **context,
    }
    entry, created = EmailDeliveryLog.objects.get_or_create(
        dedupe_key=dedupe_key[:220],
        defaults=dict(event_type=event_type, recipient=recipient, recipient_role=role, cc=sorted(set(cc or [])),
                      subject=subject[:255], template=template, context=full_ctx,
                      related_type=related.__class__.__name__ if related is not None else "",
                      related_id=str(related.pk) if related is not None else ""),
    )
    if not created:
        return entry  # duplicate event — do not send again
    pk = entry.pk
    if settings.USE_CELERY:
        from .tasks import send_email_task

        transaction.on_commit(lambda: send_email_task.delay(pk))
    else:
        transaction.on_commit(lambda: deliver(pk))
    return entry


def _render(entry):
    ctx = dict(entry.context)
    html = render_to_string(f"{entry.template}.html", ctx)
    text = render_to_string(f"{entry.template}.txt", ctx)
    return text, html


def deliver(log_id):
    with transaction.atomic():
        entry = EmailDeliveryLog.objects.select_for_update().get(pk=log_id)
        if entry.status in (EmailDeliveryLog.Status.SENT, EmailDeliveryLog.Status.FAILED):
            return entry
        if not email_configured():
            entry.status = EmailDeliveryLog.Status.NOT_CONFIGURED
            entry.last_error = "Email is not configured (EMAIL_ENABLED is off or notifications disabled). Nothing was sent."
            entry.save(update_fields=["status", "last_error", "updated_at"])
            log.warning("Email not configured — %s to %s logged but NOT sent", entry.event_type, entry.recipient)
            return entry
        entry.attempts += 1
        try:
            text, html = _render(entry)
            from_name = NotificationSettings.load().from_name
            from_email = settings.DEFAULT_FROM_EMAIL
            if from_name and "<" in from_email:
                from_email = f"{from_name} <{from_email.split('<', 1)[1]}"
            msg = EmailMultiAlternatives(entry.subject, text, from_email, [entry.recipient], cc=entry.cc or None)
            msg.attach_alternative(html, "text/html")
            accepted = msg.send(fail_silently=False)
            if accepted:
                entry.status = EmailDeliveryLog.Status.SENT
                entry.sent_at = timezone.now()
                entry.last_error = ""
            else:
                raise smtplib.SMTPException("Mail backend reported 0 messages accepted")
        except Exception as exc:  # noqa: BLE001 — every failure must be logged, never hidden
            transient = isinstance(exc, TRANSIENT_ERRORS) or (
                isinstance(exc, smtplib.SMTPResponseException) and 400 <= exc.smtp_code < 500)
            entry.last_error = f"{exc.__class__.__name__}: {str(exc)[:400]}"
            if transient and entry.attempts < settings.EMAIL_MAX_ATTEMPTS:
                entry.status = EmailDeliveryLog.Status.RETRYING
                entry.next_attempt_at = timezone.now() + timedelta(minutes=2 ** entry.attempts)
            else:
                entry.status = EmailDeliveryLog.Status.FAILED
        entry.save()
        return entry


def retry_due_emails(now=None):
    now = now or timezone.now()
    ids = list(EmailDeliveryLog.objects.filter(status=EmailDeliveryLog.Status.RETRYING, next_attempt_at__lte=now).values_list("pk", flat=True))
    for pk in ids:
        deliver(pk)
    return len(ids)


def send_transactional(*, event_type, recipient, subject, context, secret_context, template):
    """Send immediately without persisting `secret_context` (e.g. reset links). Not retried."""
    entry = EmailDeliveryLog.objects.create(
        event_type=event_type, recipient=recipient, subject=subject, template=template,
        context={k: v for k, v in context.items()}, dedupe_key=f"{event_type}:{timezone.now().timestamp()}:{recipient}"[:220],
    )
    if not email_configured():
        entry.status = EmailDeliveryLog.Status.NOT_CONFIGURED
        entry.last_error = "Email is not configured. Nothing was sent."
        entry.save()
        return entry
    entry.attempts = 1
    ctx = {"company_name": settings.COMPANY_NAME, **context, **secret_context}
    try:
        msg = EmailMultiAlternatives(subject, render_to_string(f"{template}.txt", ctx), settings.DEFAULT_FROM_EMAIL, [recipient])
        msg.attach_alternative(render_to_string(f"{template}.html", ctx), "text/html")
        msg.send(fail_silently=False)
        entry.status, entry.sent_at = EmailDeliveryLog.Status.SENT, timezone.now()
    except Exception as exc:  # noqa: BLE001
        entry.status, entry.last_error = EmailDeliveryLog.Status.FAILED, f"{exc.__class__.__name__}: {str(exc)[:400]}"
    entry.save()
    return entry


# ---------------------------------------------------------------------------
# Recipient rules
# ---------------------------------------------------------------------------
def group_addresses(code):
    g = RecipientGroup.objects.filter(code=code, is_active=True).first()
    return g.addresses() if g else []


def hr_users():
    from apps.accounts.models import Role, User

    return User.objects.filter(is_active=True).filter(role=Role.ADMIN) | User.objects.filter(is_active=True, groups__name="HR")


def approvers_for(req):
    from apps.accounts.models import Role, User

    step = req.current_step_name
    if step == "MANAGER" and req.assigned_manager_id:
        u = getattr(req.assigned_manager, "user", None)
        return [u] if u and u.is_active else []
    if step == "HR":
        return list(hr_users().distinct())
    if step == "ADMIN":
        return list(User.objects.filter(is_active=True, role=Role.ADMIN))
    return []


def _employee_user(employee):
    u = getattr(employee, "user", None)
    return u if u and u.is_active else None


def _email_of(user):
    if user is None:
        return ""
    return user.email or (user.employee.official_email if user.employee_id else "")


EVENT_TEXT = {
    "submitted": ("submitted", "A new {type} request from {name} needs your review."),
    "forwarded": ("forwarded for further approval", "{type} request from {name} has been approved at the previous step and needs your review."),
    "approved": ("approved", "Your {type} request has been approved."),
    "rejected": ("rejected", "Your {type} request has been rejected."),
    "info_requested": ("needs more information", "Your approver has asked for more information on your {type} request."),
    "responded": ("updated by the employee", "{name} has responded to your clarification request."),
    "cancelled": ("cancelled", "The {type} request from {name} has been cancelled."),
}


def notify_request_event(model_cls, pk, event, actor_pk=None, comment=""):
    from apps.approvals.models import ApprovalAction

    req = model_cls.objects.select_related("employee", "assigned_manager").get(pk=pk)
    emp = req.employee
    last_action = ApprovalAction.objects.filter(object_id=req.pk, request_type=req.workflow_type()).order_by("-id").first()
    action_id = last_action.pk if last_action else 0
    type_label = req.TYPE_LABEL if req.workflow_type() != "EMERGENCY_LEAVE" else "Emergency leave"
    verb, intro_tpl = EVENT_TEXT.get(event, (event, "{type} request updated."))
    try:
        link_path = reverse("approvals:detail", args=[req.URL_SLUG, req.pk])
    except NoReverseMatch:
        link_path = "/"
    subject = f"[{settings.COMPANY_SHORT_NAME} HR] {type_label} {verb} — {emp.employee_id} {emp.full_name}"
    context = {
        "heading": f"{type_label} {verb}",
        "employee_name": emp.full_name, "employee_id": emp.employee_id,
        "request_type": type_label, "status": req.get_status_display(),
        "details": [list(r) for r in req.notification_details()],
        "comment": comment, "link": portal_url(link_path),
    }

    def send(users, intro, role_key, cc=None):
        for u in users:
            if u is None or (actor_pk and u.pk == actor_pk and event not in ("approved", "rejected")):
                continue
            dedupe = f"{event}:{req.workflow_type()}:{req.pk}:{action_id}:{role_key}"
            notify_in_app(u, f"request.{event}", subject.split("] ", 1)[-1], intro, link_path, dedupe)
            addr = _email_of(u)
            if addr:
                queue_email(event_type=f"{req.workflow_type().lower()}.{event}", recipient=addr, subject=subject,
                            context={**context, "intro": intro, "recipient_name": u.get_full_name()},
                            dedupe_key=f"{dedupe}:{addr}", related=req, cc=cc)

    intro = intro_tpl.format(type=type_label.lower(), name=emp.full_name)
    employee_user = _employee_user(emp)

    if event in ("submitted", "forwarded", "responded"):
        approvers = approvers_for(req)
        cc = []
        if req.workflow_type() == "SHORT_LEAVE" and event == "submitted":
            ns = NotificationSettings.load()
            for g in ns.short_leave_cc_groups.filter(is_active=True):
                cc += g.addresses()
        send(approvers, intro, "approver", cc=cc)
        if event == "submitted" and employee_user and req.submitted_by_id:
            notify_in_app(employee_user, "request.submitted", f"{type_label} submitted", req.summary(), link_path,
                          f"self:{req.workflow_type()}:{req.pk}:{action_id}")
        if event == "submitted" and req.workflow_type() == "HALF_DAY_REVIEW" and employee_user:
            send([employee_user], "Your attendance was flagged under the half-day rule. You can add an explanation for your manager.", "employee")
        if req.workflow_type() == "SHORT_LEAVE" and event == "submitted":
            _notify_team_short_leave(req, action_id)
    elif event in ("approved", "rejected", "info_requested"):
        send([employee_user], intro, "employee")
    elif event == "cancelled":
        send(approvers_for(req) if req.is_open else [], intro, "approver")
        send([employee_user], f"Your {type_label.lower()} request has been cancelled.", "employee")


def _notify_team_short_leave(req, action_id):
    """Attendance policy: inform the team. Team members get minimal data only (no reason)."""
    ns = NotificationSettings.load()
    if not ns.short_leave_notify_team or not req.employee.reporting_manager_id:
        return
    from apps.employees.models import Employee

    peers = Employee.objects.filter(reporting_manager_id=req.employee.reporting_manager_id, is_active=True).exclude(pk=req.employee_id)
    subject = f"[{settings.COMPANY_SHORT_NAME} HR] Team update — {req.employee.full_name} short leave on {req.date:%d %b}"
    for peer in peers:
        u = _employee_user(peer)
        addr = _email_of(u) if u else peer.official_email
        if not addr:
            continue
        queue_email(event_type="short_leave.team_notice", recipient=addr, subject=subject,
                    context={"heading": "Team availability update", "intro": f"{req.employee.full_name} has requested short leave.",
                             "details": [["Date", f"{req.date:%d %b %Y}"], ["Time", f"{req.start_time:%H:%M} – {req.end_time:%H:%M}"]],
                             "recipient_name": peer.full_name, "link": portal_url("/")},
                    dedupe_key=f"team:SHORT_LEAVE:{req.pk}:{action_id}:{addr}", related=req)
    req.__class__.objects.filter(pk=req.pk).update(team_notified=True)


def notify_users(users, *, event_type, title, intro, details=None, link_path="/", dedupe, related=None, email=True):
    for u in users:
        if u is None or not u.is_active:
            continue
        notify_in_app(u, event_type, title, intro, link_path, f"{dedupe}")
        addr = _email_of(u)
        if email and addr:
            queue_email(event_type=event_type, recipient=addr, subject=f"[{settings.COMPANY_SHORT_NAME} HR] {title}",
                        context={"heading": title, "intro": intro, "details": details or [], "recipient_name": u.get_full_name(),
                                 "link": portal_url(link_path)},
                        dedupe_key=f"{dedupe}:{addr}", related=related)
