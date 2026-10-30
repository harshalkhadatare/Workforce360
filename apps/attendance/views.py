import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.approvals.models import OPEN_STATUSES, Status
from apps.core import permissions as perms
from apps.core.utils import paginate
from apps.policies.services import PolicyNotConfigured, get_schedule

from . import request_services as rs
from .forms import AdminCorrectionForm, MissedPunchForm, OnDutyForm, ShortLeaveForm
from .models import AttendanceAdjustment, AttendancePunch, AttendanceRecord, HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
from .services import admin_correction, display_status, monthly_grace_summary, recalculate_month


def _target_employee(request):
    """Self by default; another employee only if within the user's scope (404 otherwise)."""
    emp_id = request.GET.get("employee")
    if emp_id:
        return perms.get_scoped_employee(request.user, emp_id)
    emp = perms.own_employee(request.user)
    if not emp:
        raise PermissionDenied("Your login is not linked to an employee record.")
    return emp


def _form_errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errs in exc.error_dict.items():
            form.add_error(field if field in form.fields else None, errs)
    else:
        form.add_error(None, exc)


@login_required
def day_detail(request, day):
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        raise PermissionDenied
    emp = _target_employee(request)
    rec = AttendanceRecord.objects.filter(employee=emp, date=d).select_related("policy_version", "schedule").first()
    try:
        sched = get_schedule(emp, d)
    except PolicyNotConfigured:
        sched = None
    punches = AttendancePunch.objects.filter(employee=emp, punch_date=d).select_related("batch").order_by("punch_time")
    adjustments = AttendanceAdjustment.objects.filter(employee=emp, date=d).select_related("approved_by")
    from apps.leave_management.models import LeaveRequest

    leave = LeaveRequest.objects.filter(employee=emp, start_date__lte=d, end_date__gte=d).exclude(status=Status.CANCELLED).first()
    sl = ShortLeaveRequest.objects.filter(employee=emp, date=d).exclude(status=Status.CANCELLED)
    mp = MissedPunchRequest.objects.filter(employee=emp, date=d).exclude(status=Status.CANCELLED)
    od = [o for o in OnDutyRequest.objects.filter(employee=emp).exclude(status=Status.CANCELLED) if d in o.dates()]
    review = rec.open_review if rec else None
    corr_form = None
    if perms.is_hr(request.user):
        corr_form = AdminCorrectionForm(request.POST or None)
        if request.method == "POST" and corr_form.is_valid():
            try:
                admin_correction(emp, d, corr_form.cleaned_data["corrected_in"], corr_form.cleaned_data["corrected_out"],
                                 corr_form.cleaned_data["reason"], request.user)
                messages.success(request, "Correction recorded. Original punches were preserved.")
                return redirect(request.get_full_path())
            except (ValueError, ValidationError) as e:
                corr_form.add_error(None, str(e))
    prev_day, next_day = d - datetime.timedelta(days=1), d + datetime.timedelta(days=1)
    return render(request, "attendance/day_detail.html", {
        "emp": emp, "d": d, "rec": rec, "rec_status": display_status(rec) if rec else None, "sched": sched, "punches": punches,
        "adjustments": adjustments, "leave": leave, "short_leaves": sl, "missed_punches": mp, "on_duty": od, "review": review,
        "corr_form": corr_form, "prev_day": prev_day, "next_day": next_day, "is_self": emp == perms.own_employee(request.user),
        "can_view_punch_source": perms.is_hr(request.user),
    })


@login_required
def history(request):
    emp = _target_employee(request)
    today = timezone.localdate()
    try:
        y, m = [int(x) for x in request.GET.get("month", f"{today:%Y-%m}").split("-")]
        first = datetime.date(y, m, 1)
    except (ValueError, TypeError):
        first = today.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    if perms.is_hr(request.user) and request.method == "POST" and request.POST.get("action") == "recalc":
        recalculate_month(emp, first.year, first.month)
        messages.success(request, "Month recalculated from punches and approved requests.")
        return redirect(f"{request.path}?employee={emp.pk}&month={first:%Y-%m}")
    recs = AttendanceRecord.objects.filter(employee=emp, date__gte=first, date__lte=last).order_by("date")
    rows = [(r, display_status(r)) for r in recs]
    prev_m = (first - datetime.timedelta(days=1)).replace(day=1)
    next_m = last + datetime.timedelta(days=1)
    return render(request, "attendance/history.html", {
        "emp": emp, "rows": rows, "first": first, "prev_m": prev_m, "next_m": next_m if next_m <= today else None,
        "grace": monthly_grace_summary(emp, first.year, first.month), "is_self": emp == perms.own_employee(request.user),
        "totals": {"present": recs.filter(status__in=["PRESENT", "HALF_DAY_FLAG", "ON_DUTY"]).count(), "absent": recs.filter(status="ABSENT").count(),
                   "leave": recs.filter(status__in=["LEAVE", "HALF_DAY_LEAVE"]).count(), "incomplete": recs.filter(status="INCOMPLETE").count(),
                   "late": recs.filter(late_minutes__gt=0).count(), "flags": recs.filter(half_day_flag=True).count()},
    })


@perms.manager_required
def team(request):
    today = timezone.localdate()
    try:
        d = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        d = today
    if perms.is_hr(request.user) and request.GET.get("scope") != "team":
        emps = perms.employee_scope(request.user).filter(is_active=True)
    else:
        emps = perms.team_queryset(request.user).filter(is_active=True)
    recs = AttendanceRecord.objects.filter(employee__in=emps, date=d).select_related("employee", "employee__department")
    status = request.GET.get("status")
    if status:
        recs = recs.filter(status=status) if status != "PRESENT" else recs.filter(status__in=["PRESENT", "HALF_DAY_FLAG", "ON_DUTY"])
    if request.GET.get("flag") == "late":
        recs = recs.filter(late_minutes__gt=0)
    dept = request.GET.get("department")
    if dept:
        recs = recs.filter(employee__department_id=dept)
    missing = emps.exclude(pk__in=AttendanceRecord.objects.filter(date=d).values("employee_id")).count()
    from apps.employees.models import Department

    return render(request, "attendance/team.html", {
        "d": d, "page_obj": paginate(request, [(r, display_status(r)) for r in recs.order_by("employee__employee_id")], 50),
        "statuses": AttendanceRecord.DayStatus.choices, "missing": missing, "departments": Department.objects.filter(is_active=True),
        "company": perms.is_hr(request.user) and request.GET.get("scope") != "team",
    })


@perms.employee_required
def short_leave(request):
    emp = perms.own_employee(request.user)
    form = ShortLeaveForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            req = rs.submit_short_leave(emp, request.user, **form.cleaned_data)
            messages.success(request, "Short leave submitted. Your manager has been notified; HR and Management are copied per policy.")
            return redirect("approvals:detail", slug=req.URL_SLUG, pk=req.pk)
        except ValidationError as e:
            _form_errors(form, e)
    today = timezone.localdate()
    used = ShortLeaveRequest.objects.filter(employee=emp, date__year=today.year, date__month=today.month).exclude(
        status__in=[Status.REJECTED, Status.CANCELLED]).count()
    return render(request, "attendance/request_form.html", {
        "form": form, "title": "Apply for Short Leave", "icon": "bi-hourglass-split",
        "intro": "Short leave is limited to two hours per request and two requests per month. Request it before you leave, "
                 "or by the end of the same working day for a late arrival.",
        "side": [("Used this month", f"{used} of 2"), ("Notifications", "Reporting manager & team; HR and Management in CC")],
    })


@perms.employee_required
def missed_punch(request):
    emp = perms.own_employee(request.user)
    initial = {"date": request.GET.get("date")}
    form = MissedPunchForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            req = rs.submit_missed_punch(emp, request.user, **form.cleaned_data)
            messages.success(request, "Missed-punch request submitted to your reporting manager.")
            return redirect("approvals:detail", slug=req.URL_SLUG, pk=req.pk)
        except ValidationError as e:
            _form_errors(form, e)
    rec = None
    if initial["date"]:
        try:
            rec = AttendanceRecord.objects.filter(employee=emp, date=datetime.date.fromisoformat(initial["date"])).first()
        except ValueError:
            pass
    return render(request, "attendance/request_form.html", {
        "form": form, "title": "Report Missed Punch", "icon": "bi-fingerprint", "rec": rec, "conditional": True,
        "intro": "Report a missing or incorrect punch. Your manager reviews the request; an approved correction is recorded as an audited "
                 "adjustment and the original imported punch is kept.",
    })


@perms.employee_required
def on_duty(request):
    emp = perms.own_employee(request.user)
    form = OnDutyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            req = rs.submit_on_duty(emp, request.user, **form.cleaned_data)
            messages.success(request, "On-duty request submitted for approval.")
            return redirect("approvals:detail", slug=req.URL_SLUG, pk=req.pk)
        except ValidationError as e:
            _form_errors(form, e)
    return render(request, "attendance/request_form.html", {
        "form": form, "title": "Submit On-Duty Request", "icon": "bi-briefcase",
        "intro": "For official work outside your designated work location. Approval is required before you leave the office or worksite.",
    })


@perms.employee_required
def half_day_flags(request):
    emp = perms.own_employee(request.user)
    reviews = HalfDayReview.objects.filter(employee=emp).select_related("attendance_record").order_by("-attendance_record__date")
    return render(request, "attendance/half_day_flags.html", {"page_obj": paginate(request, reviews), "open_statuses": OPEN_STATUSES})
