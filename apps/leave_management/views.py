import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.approvals import registry
from apps.approvals.models import OPEN_STATUSES, Status
from apps.attendance.models import AttendanceRecord, HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
from apps.attendance.services import monthly_grace_summary
from apps.core import permissions as perms
from apps.policies.models import Holiday, PolicyVersion
from apps.policies.services import PolicyNotConfigured, daterange, get_policy, get_schedule, holidays_between

from . import services
from .forms import AdjustmentForm, LeaveApplyForm
from .models import LeaveLedgerEntry, LeaveRequest, LeaveType


def _target(request):
    emp_id = request.GET.get("employee")
    if emp_id:
        return perms.get_scoped_employee(request.user, emp_id)
    emp = perms.own_employee(request.user)
    if not emp:
        raise PermissionDenied
    return emp


@perms.employee_required
def dashboard(request):
    emp = perms.own_employee(request.user)
    today = timezone.localdate()
    bals = services.balances_for(emp, today.year)
    recs = AttendanceRecord.objects.filter(employee=emp, date__gte=today - datetime.timedelta(days=60))
    ctx = {
        "emp": emp, "today": today, "balances": bals,
        "available": sum(b["available"] for b in bals), "used": sum(b["used"] for b in bals), "pending_days": sum(b["pending"] for b in bals),
        "pending_leave": LeaveRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "upcoming": LeaveRequest.objects.filter(employee=emp, status=Status.APPROVED, start_date__gte=today).order_by("start_date")[:5],
        "rejected": LeaveRequest.objects.filter(employee=emp, status=Status.REJECTED, start_date__year=today.year).count(),
        "pending_short": ShortLeaveRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_missed": MissedPunchRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_od": OnDutyRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_halfday": HalfDayReview.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "grace": monthly_grace_summary(emp, today.year, today.month),
        "discrepancies": recs.filter(Q(status__in=["INCOMPLETE", "ABSENT"]) | Q(half_day_flag=True)).order_by("-date")[:8],
        "has_attendance": AttendanceRecord.objects.filter(employee=emp).exists(),
        "chart": [{"label": b["leave_type"].name, "used": float(b["used"]), "pending": float(b["pending"]), "available": float(max(b["available"], 0))} for b in bals],
    }
    return render(request, "lms/dashboard.html", ctx)


@login_required
def calendar_page(request):
    scope = request.GET.get("scope", "me")
    if scope == "team" and not perms.is_manager(request.user):
        raise PermissionDenied
    if scope == "company" and not perms.is_hr(request.user):
        raise PermissionDenied
    if scope == "me" and not perms.own_employee(request.user):
        scope = "company" if perms.is_hr(request.user) else "me"
    emp = None
    if request.GET.get("employee"):
        emp = perms.get_scoped_employee(request.user, request.GET["employee"])
    return render(request, "lms/calendar.html", {"scope": scope, "emp": emp})


CATEGORY_STYLE = {
    "PRESENT": ("#e8f6f0", "#12805c"), "HALF_DAY_FLAG": ("#fdf4e3", "#a15c07"), "ABSENT": ("#fdecea", "#c0352b"),
    "INCOMPLETE": ("#fdf4e3", "#a15c07"), "LEAVE": ("#edf2ff", "#1849c9"), "HALF_DAY_LEAVE": ("#edf2ff", "#1849c9"),
    "ON_DUTY": ("#e6f5f3", "#0f766e"), "WEEKLY_OFF": ("#f1f3f6", "#556274"), "HOLIDAY": ("#f1ecfd", "#6d3fd1"),
    "PENDING_LEAVE": ("#fff", "#1849c9"), "REJECTED_LEAVE": ("#fff", "#c0352b"), "SHORT_LEAVE": ("#f1ecfd", "#6d3fd1"),
    "EXCEPTION": ("#fdecea", "#c0352b"),
}
CATEGORY_LABEL = {"PRESENT": "Present", "HALF_DAY_FLAG": "Half day (flag)", "ABSENT": "Absent", "INCOMPLETE": "Missing punch",
                  "LEAVE": "Leave", "HALF_DAY_LEAVE": "Half-day leave", "ON_DUTY": "On duty", "WEEKLY_OFF": "Weekly off",
                  "HOLIDAY": "Holiday", "NOT_IMPORTED": None}


def _event(title, start, cat, end=None, url=None, all_day=True, extra=None):
    bg, fg = CATEGORY_STYLE.get(cat, ("#f1f3f6", "#556274"))
    ev = {"title": title, "start": start.isoformat(), "allDay": all_day, "backgroundColor": bg, "borderColor": fg if bg == "#fff" else bg,
          "textColor": fg, "extendedProps": {"category": cat, **(extra or {})}}
    if end:
        ev["end"] = end.isoformat()
    if url:
        ev["url"] = url
    return ev


@api_view(["GET"])
def calendar_events(request):
    """FullCalendar feed. Events are built only from database records within the caller's scope."""
    try:
        start = datetime.date.fromisoformat(request.GET["start"][:10])
        end = datetime.date.fromisoformat(request.GET["end"][:10])
    except (KeyError, ValueError):
        return Response({"detail": "start and end are required"}, status=400)
    if (end - start).days > 62:
        return Response({"detail": "Range too large"}, status=400)
    scope = request.GET.get("scope", "me")
    user = request.user
    events = []
    me = perms.own_employee(user)

    for h in Holiday.objects.filter(is_active=True, date__gte=start, date__lt=end):
        events.append(_event(f"{h.name}", h.date, "HOLIDAY", extra={"detail": h.get_kind_display() + (f" · {h.location}" if h.location else "")}))

    if scope == "me" or request.GET.get("employee"):
        emp = perms.get_scoped_employee(user, request.GET["employee"]) if request.GET.get("employee") else me
        if emp is None:
            return Response([])
        try:
            for d in daterange(start, end - datetime.timedelta(days=1)):
                s = get_schedule(emp, d)
                if d.weekday() in (s.weekly_off_days or []):
                    events.append(_event("Weekly off", d, "WEEKLY_OFF", extra={"detail": s.name}))
        except PolicyNotConfigured:
            pass
        for r in AttendanceRecord.objects.filter(employee=emp, date__gte=start, date__lt=end).exclude(status__in=["WEEKLY_OFF", "HOLIDAY", "NOT_IMPORTED"]):
            label = CATEGORY_LABEL.get(r.status) or r.get_status_display()
            if r.first_in and r.status in ("PRESENT", "HALF_DAY_FLAG", "HALF_DAY_LEAVE", "INCOMPLETE", "ON_DUTY"):
                label += f" · {timezone.localtime(r.first_in):%H:%M}–{timezone.localtime(r.last_out):%H:%M}" if r.last_out else f" · {timezone.localtime(r.first_in):%H:%M}–?"
            cat = r.status
            if r.late_minutes and r.status == "PRESENT":
                label += f" · late {r.late_minutes}m"
            events.append(_event(label, r.date, cat, url=f"/attendance/day/{r.date:%Y-%m-%d}/" + (f"?employee={emp.pk}" if emp != me else ""),
                                 extra={"detail": r.explanation or r.notes}))
        leaves = LeaveRequest.objects.filter(employee=emp, start_date__lt=end, end_date__gte=start).exclude(status=Status.CANCELLED)
        show_rejected = emp == me or perms.is_hr(user)
        for lr in leaves:
            if lr.status == Status.REJECTED and not show_rejected:
                continue
            cat = "LEAVE" if lr.status == Status.APPROVED else ("REJECTED_LEAVE" if lr.status == Status.REJECTED else "PENDING_LEAVE")
            prefix = {"LEAVE": "", "PENDING_LEAVE": "Pending: ", "REJECTED_LEAVE": "Rejected: "}[cat]
            events.append(_event(f"{prefix}{lr.leave_type.name}{' (½)' if lr.is_half_day else ''}", lr.start_date, cat,
                                 end=lr.end_date + datetime.timedelta(days=1), url=f"/approvals/r/leave/{lr.pk}/",
                                 extra={"detail": lr.get_status_display()}))
        for sl in ShortLeaveRequest.objects.filter(employee=emp, date__gte=start, date__lt=end).exclude(status__in=[Status.CANCELLED, Status.REJECTED]):
            events.append(_event(f"Short leave {sl.start_time:%H:%M}–{sl.end_time:%H:%M}" + ("" if sl.status == Status.APPROVED else " (pending)"),
                                 sl.date, "SHORT_LEAVE", url=f"/approvals/r/short-leave/{sl.pk}/"))
        for od in OnDutyRequest.objects.filter(employee=emp).exclude(status__in=[Status.CANCELLED, Status.REJECTED]):
            for d in od.dates():
                if start <= d < end:
                    events.append(_event(f"On duty: {od.destination}" + ("" if od.status == Status.APPROVED else " (pending)"), d, "ON_DUTY",
                                         url=f"/approvals/r/on-duty/{od.pk}/"))
        return Response(events)

    # Team / company: names and leave types only — no reasons or confidential details.
    if scope == "team":
        if not perms.is_manager(user):
            return Response({"detail": "Forbidden"}, status=403)
        emps = perms.team_queryset(user).filter(is_active=True)
    elif scope == "company":
        if not perms.is_hr(user):
            return Response({"detail": "Forbidden"}, status=403)
        emps = perms.employee_scope(user).filter(is_active=True)
    else:
        return Response({"detail": "Unknown scope"}, status=400)
    for lr in LeaveRequest.objects.filter(employee__in=emps, start_date__lt=end, end_date__gte=start,
                                          status__in=list(OPEN_STATUSES) + [Status.APPROVED]).select_related("employee", "leave_type"):
        cat = "LEAVE" if lr.status == Status.APPROVED else "PENDING_LEAVE"
        events.append(_event(f"{lr.employee.full_name} — {'Leave' if cat == 'LEAVE' else 'Leave (pending)'}", lr.start_date, cat,
                             end=lr.end_date + datetime.timedelta(days=1), url=f"/approvals/r/leave/{lr.pk}/", extra={"detail": lr.leave_type.name}))
    for od in OnDutyRequest.objects.filter(employee__in=emps, status=Status.APPROVED).select_related("employee"):
        for d in od.dates():
            if start <= d < end:
                events.append(_event(f"{od.employee.full_name} — On duty", d, "ON_DUTY"))
    for sl in ShortLeaveRequest.objects.filter(employee__in=emps, date__gte=start, date__lt=end, status=Status.APPROVED).select_related("employee"):
        events.append(_event(f"{sl.employee.full_name} — Short leave", sl.date, "SHORT_LEAVE"))
    for r in AttendanceRecord.objects.filter(employee__in=emps, date__gte=start, date__lt=end).filter(
            Q(status__in=["ABSENT", "INCOMPLETE"]) | Q(half_day_flag=True)).select_related("employee"):
        events.append(_event(f"{r.employee.full_name} — {r.get_status_display()}", r.date, "EXCEPTION",
                             url=f"/attendance/day/{r.date:%Y-%m-%d}/?employee={r.employee_id}"))
    return Response(events)


@login_required
def balance_page(request):
    emp = _target(request)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year
    is_hr = perms.is_hr(request.user)
    adj_form = AdjustmentForm(request.POST or None) if is_hr else None
    if is_hr and request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "adjust" and adj_form.is_valid():
                d = adj_form.cleaned_data
                services.manual_adjustment(emp, d["leave_type"], d["quantity"], d["effective_date"], d["reason"], request.user, d["entry_type"])
                messages.success(request, "Ledger entry posted.")
                return redirect(request.get_full_path())
            if action == "entitlements":
                created = services.post_entitlements(emp, year, request.user)
                messages.success(request, f"{len(created)} entitlement credit(s) posted for {year}." if created else "No new entitlements were due.")
                return redirect(request.get_full_path())
        except (ValidationError, PolicyNotConfigured) as e:
            messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
    bals = services.balances_for(emp, year)
    ledger = LeaveLedgerEntry.objects.filter(employee=emp, leave_year=year).select_related("leave_type", "leave_request", "created_by").order_by("effective_date", "id")
    preview = None
    if is_hr:
        try:
            preview = services.compute_entitlements(emp, year)
        except PolicyNotConfigured as e:
            preview = str(e)
    return render(request, "lms/balance.html", {
        "emp": emp, "year": year, "balances": bals, "ledger": ledger, "is_hr": is_hr, "adj_form": adj_form, "preview": preview,
        "entitlements": emp.leave_entitlements.filter(leave_year=year), "is_self": emp == perms.own_employee(request.user),
        "chart": [{"label": b["leave_type"].name, "used": float(b["used"]), "pending": float(b["pending"]), "available": float(max(b["available"], 0))} for b in bals],
    })


@perms.employee_required
def apply(request):
    emp = perms.own_employee(request.user)
    emergency = request.GET.get("emergency") == "1" or request.POST.get("is_emergency") == "1"
    form = LeaveApplyForm(request.POST or None, request.FILES or None, emergency=emergency)
    try:
        policy = get_policy(PolicyVersion.Kind.LEAVE, timezone.localdate(), emp)
    except PolicyNotConfigured:
        policy = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            req = services.apply_for_leave(
                emp, request.user, leave_type=d["leave_type"], start_date=d["start_date"], end_date=d["end_date"],
                duration_type=d["duration_type"], half_day_session=d.get("half_day_session") or "", reason=d["reason"],
                attachment=d.get("attachment"), is_emergency=emergency, rejoining_date=d.get("rejoining_date"), signed_form=d.get("signed_form"))
            for w in getattr(req, "_warnings", []):
                messages.warning(request, w)
            if req.deadline_breached:
                messages.warning(request, "The signed form was submitted after the deadline and has been flagged for HR review.")
            messages.success(request, f"Leave request submitted ({req.days_requested.normalize()} day(s)). Your reporting manager has been notified.")
            return redirect("approvals:detail", slug="leave", pk=req.pk)
        except ValidationError as e:
            if hasattr(e, "error_dict"):
                for field, errs in e.error_dict.items():
                    form.add_error(field if field in form.fields else None, errs)
            else:
                form.add_error(None, e)
    today = timezone.localdate()
    return render(request, "lms/apply.html", {"form": form, "emergency": emergency, "policy": policy,
                                              "balances": services.balances_for(emp, today.year), "manager": emp.reporting_manager,
                                              "notice_days": policy.get("advance_notice_days") if policy else None,
                                              "deadline_days": policy.get("emergency_form_deadline_days") if policy else 2})


@perms.employee_required
def my_requests(request):
    emp = perms.own_employee(request.user)
    status = request.GET.get("status")
    statuses = OPEN_STATUSES if status == "open" else ([status] if status else None)
    items = registry.my_requests(emp, statuses)
    t = request.GET.get("type")
    if t:
        items = [r for r in items if r.URL_SLUG == t]
    return render(request, "lms/my_requests.html", {"items": items, "type": t, "status": status,
                                                    "types": [(s, m.TYPE_LABEL) for s, m in registry.request_models().items()],
                                                    "statuses": Status.choices})


@login_required
def holidays(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year
    hs = Holiday.objects.filter(date__year=year, is_active=True)
    return render(request, "lms/holidays.html", {"holidays": hs, "year": year, "today": today})
