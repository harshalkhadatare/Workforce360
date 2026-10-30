import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core import permissions as perms
from apps.core.utils import private_file_response

from . import registry, services as wf
from .models import OPEN_STATUSES, PENDING_STATUSES, Status


def _load(request, slug, pk):
    model = registry.model_for(slug)
    try:
        req = model.objects.select_related("employee", "assigned_manager").get(pk=pk)
    except model.DoesNotExist:
        raise Http404
    if not wf.can_view_request(request.user, req):
        raise Http404
    return req


def _context_for(req):
    """Employee & attendance context shown to reviewers."""
    from apps.attendance.models import AttendanceRecord
    from apps.leave_management.services import balance

    ctx = {}
    emp = req.employee
    slug = req.URL_SLUG
    if slug == "leave":
        ctx["balance"] = balance(emp, req.leave_type, req.start_date.year) if req.leave_type.tracks_balance else None
        ctx["breakdown"] = req.day_breakdown
        ctx["recent_leave"] = req.__class__.objects.filter(employee=emp, status=Status.APPROVED).exclude(pk=req.pk).order_by("-start_date")[:5]
    d = getattr(req, "date", None) or (getattr(req, "start_date", None))
    if d is None and hasattr(req, "start_at"):
        d = timezone.localtime(req.start_at).date()
    if d:
        start = d - datetime.timedelta(days=3)
        ctx["attendance_window"] = AttendanceRecord.objects.filter(employee=emp, date__gte=start, date__lte=d + datetime.timedelta(days=3)).order_by("date")
        ctx["record"] = AttendanceRecord.objects.filter(employee=emp, date=d).first()
    if slug == "short-leave":
        ctx["month_count"] = req.__class__.objects.filter(employee=emp, date__year=req.date.year, date__month=req.date.month).exclude(
            status__in=[Status.REJECTED, Status.CANCELLED]).count()
    return ctx


@login_required
def detail(request, slug, pk):
    req = _load(request, slug, pk)
    me = perms.own_employee(request.user)
    is_owner = bool(me and me.pk == req.employee_id)
    can_cancel = (is_owner and req.status in OPEN_STATUSES) or (perms.is_hr(request.user) and not is_owner and req.status in OPEN_STATUSES + (Status.APPROVED,))
    ctx = {"req": req, "slug": slug, "history": req.history(), "can_act": wf.can_act(request.user, req), "is_owner": is_owner,
           "can_cancel": can_cancel, "can_respond": is_owner and req.status == Status.NEEDS_INFORMATION,
           "is_reviewer": not is_owner, **_context_for(req)}
    if slug == "leave":
        ctx["can_upload_form"] = is_owner and req.is_emergency and not req.signed_form and req.status not in (Status.CANCELLED, Status.REJECTED)
    if slug == "half-day":
        ctx["can_explain"] = is_owner and req.is_open
    return render(request, "approvals/detail.html", ctx)


@login_required
@require_POST
def act(request, slug, pk):
    req = _load(request, slug, pk)
    action = request.POST.get("action")
    comment = request.POST.get("comment", "")
    try:
        if action in ("APPROVE", "REJECT", "REQUEST_INFO"):
            wf.decide(req, request.user, action, comment)
            label = {"APPROVE": "approved", "REJECT": "rejected", "REQUEST_INFO": "sent back for clarification"}[action]
            if slug == "half-day":
                label = {"APPROVE": "waived", "REJECT": "confirmed as half day", "REQUEST_INFO": "sent back for clarification"}[action]
            messages.success(request, f"{req.TYPE_LABEL} {label}.")
        elif action == "RESPOND":
            wf.respond(req, request.user, comment)
            messages.success(request, "Your response has been sent to the approver.")
        elif action == "CANCEL":
            wf.cancel(req, request.user, comment)
            messages.success(request, "Request cancelled.")
        elif action == "REMARK":
            wf.add_remark(req, request.user, comment)
            messages.success(request, "Remark added.")
        elif action == "EXPLAIN" and slug == "half-day":
            from apps.attendance.request_services import add_half_day_explanation

            add_half_day_explanation(req, request.user, comment)
            messages.success(request, "Explanation added for your manager.")
        elif action == "SIGNED_FORM" and slug == "leave":
            from apps.core.validators import validate_document
            from apps.leave_management.services import upload_signed_form

            f = request.FILES.get("signed_form")
            if not f:
                raise ValidationError("Choose the signed leave form to upload.")
            validate_document(f)
            if not (perms.own_employee(request.user) and perms.own_employee(request.user).pk == req.employee_id):
                raise ValidationError("Only the employee can upload the signed form.")
            r = upload_signed_form(req, f, request.user)
            if r.deadline_breached:
                messages.warning(request, "Signed form uploaded after the deadline. It has been flagged for HR review.")
            else:
                messages.success(request, "Signed form uploaded.")
        else:
            raise ValidationError("Unknown action.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("approvals:detail", slug=slug, pk=pk)


@login_required
def attachment(request, slug, pk, field):
    req = _load(request, slug, pk)
    if field not in ("attachment", "signed_form"):
        raise Http404
    f = getattr(req, field, None)
    if getattr(req, "attachment_is_cctv", False) and field == "attachment":
        me = perms.own_employee(request.user)
        reviewer = wf.can_act(request.user, req) or perms.is_hr(request.user) or (me and req.assigned_manager_id == me.pk)
        owner = me and me.pk == req.employee_id
        if not (reviewer or owner):
            raise Http404
        audit.log(request.user, "evidence.cctv_viewed", req, employee_ref=req.employee.employee_id)
    else:
        audit.log(request.user, "evidence.viewed", req, employee_ref=req.employee.employee_id, after={"field": field})
    return private_file_response(f)


@perms.manager_required
def pending(request):
    include_all = request.GET.get("all") == "1" and perms.is_admin(request.user)
    items = registry.pending_for(request.user, include_manager_override=include_all)
    t = request.GET.get("type")
    if t:
        items = [r for r in items if r.URL_SLUG == t]
    return render(request, "approvals/pending.html", {"items": items, "type": t, "include_all": include_all,
                                                      "types": [(s, m.TYPE_LABEL) for s, m in registry.request_models().items()]})


@perms.manager_required
def manager_dashboard(request):
    from apps.attendance.models import AttendanceRecord

    me = perms.own_employee(request.user)
    team = perms.team_queryset(request.user).filter(is_active=True)
    today = timezone.localdate()
    pend = registry.pending_for(request.user)
    by_type = {}
    for r in pend:
        by_type.setdefault(r.URL_SLUG, []).append(r)
    awaiting_info = registry.team_requests(request.user, statuses=[Status.NEEDS_INFORMATION])
    recent = registry.team_requests(request.user, statuses=[Status.APPROVED, Status.REJECTED], limit=10)
    recs = AttendanceRecord.objects.filter(employee__in=team, date=today)
    month_flags = AttendanceRecord.objects.filter(employee__in=team, date__year=today.year, date__month=today.month)
    ctx = {
        "me": me, "team_size": team.count(), "pending_total": len(pend), "by_type": by_type,
        "type_labels": {s: m.TYPE_LABEL for s, m in registry.request_models().items()},
        "awaiting_info": awaiting_info, "recent": recent,
        "today_present": recs.filter(status__in=["PRESENT", "HALF_DAY_FLAG", "ON_DUTY"]).count(),
        "today_absent": recs.filter(status="ABSENT").count(), "today_incomplete": recs.filter(status="INCOMPLETE").count(),
        "today_has_data": recs.exclude(status="NOT_IMPORTED").exists(), "today": today,
        "grace_exceptions": month_flags.filter(half_day_flag=True).select_related("employee").order_by("-date")[:10],
        "late_count": month_flags.filter(late_minutes__gt=0).count(),
    }
    return render(request, "approvals/manager_dashboard.html", ctx)
