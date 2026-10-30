"""Registry of approvable request models and queries over them."""
from django.db.models import Q
from django.http import Http404

from apps.core.permissions import is_admin, is_hr, own_employee

from .models import OPEN_STATUSES, Status


def request_models():
    from apps.attendance.models import HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
    from apps.leave_management.models import LeaveRequest

    return {m.URL_SLUG: m for m in (LeaveRequest, ShortLeaveRequest, MissedPunchRequest, OnDutyRequest, HalfDayReview)}


def model_for(slug):
    try:
        return request_models()[slug]
    except KeyError:
        raise Http404("Unknown request type")


def actionable_q(user, include_manager_override=False):
    """Q object selecting requests `user` can act on now (mirrors approvals.services.can_act)."""
    me = own_employee(user)
    q = Q(pk__in=[])
    if me:
        q |= Q(status=Status.PENDING_MANAGER, assigned_manager=me)
    if is_hr(user):
        q |= Q(status=Status.PENDING_HR)
    if is_admin(user):
        q |= Q(status=Status.PENDING_ADMIN)
        if include_manager_override:
            q |= Q(status=Status.PENDING_MANAGER)
    if me:
        q &= ~Q(employee=me)
    return q


def pending_for(user, include_manager_override=False):
    out = []
    for slug, model in request_models().items():
        qs = model.objects.filter(actionable_q(user, include_manager_override)).select_related("employee", "employee__department")
        for r in qs:
            out.append(r)
    out.sort(key=lambda r: r.submitted_at or r.created_at)
    return out


def pending_counts(user):
    return {slug: model.objects.filter(actionable_q(user)).count() for slug, model in request_models().items()}


def team_requests(user, statuses=None, limit=None):
    me = own_employee(user)
    out = []
    if not me:
        return out
    for slug, model in request_models().items():
        qs = model.objects.filter(assigned_manager=me).select_related("employee")
        if statuses:
            qs = qs.filter(status__in=statuses)
        out.extend(qs.order_by("-updated_at")[: limit or 200])
    out.sort(key=lambda r: r.updated_at, reverse=True)
    return out[:limit] if limit else out


def my_requests(employee, statuses=None):
    out = []
    for slug, model in request_models().items():
        qs = model.objects.filter(employee=employee)
        if statuses:
            qs = qs.filter(status__in=statuses)
        out.extend(qs)
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def open_count(employee, slug):
    return request_models()[slug].objects.filter(employee=employee, status__in=OPEN_STATUSES).count()
