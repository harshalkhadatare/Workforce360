import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from apps.approvals import registry
from apps.approvals.models import OPEN_STATUSES, PENDING_STATUSES, Status
from apps.core.permissions import hr_required, is_hr, is_manager, own_employee


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)


@login_required
def home(request):
    """Personal dashboard (employee self-service home)."""
    from apps.attendance.models import AttendanceRecord, HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
    from apps.attendance.services import display_status, monthly_grace_summary
    from apps.leave_management.models import LeaveRequest
    from apps.leave_management.services import balances_for
    from apps.policies.services import PolicyNotConfigured, get_schedule
    from apps.queries.models import EmployeeQuery

    emp = own_employee(request.user)
    ctx = {"emp": emp}
    if emp is None:
        ctx["no_employee"] = True
        if is_hr(request.user):
            return admin_dashboard(request)
        return render(request, "dashboard/home.html", ctx)
    today = timezone.localdate()
    rec = AttendanceRecord.objects.filter(employee=emp, date=today).exclude(status=AttendanceRecord.DayStatus.NOT_IMPORTED).first()
    try:
        sched = get_schedule(emp, today)
    except PolicyNotConfigured:
        sched = None
    last_rec = AttendanceRecord.objects.filter(employee=emp).exclude(status=AttendanceRecord.DayStatus.NOT_IMPORTED).order_by("-date").first()
    balances = balances_for(emp, today.year)
    ctx.update({
        "today": today, "rec": rec, "rec_status": display_status(rec) if rec else None, "sched": sched, "last_rec": last_rec,
        "grace": monthly_grace_summary(emp, today.year, today.month),
        "balances": balances,
        "available_total": sum(b["available"] for b in balances), "used_total": sum(b["used"] for b in balances),
        "pending_leave": LeaveRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "upcoming_leave": LeaveRequest.objects.filter(employee=emp, status=Status.APPROVED, end_date__gte=today).order_by("start_date")[:5],
        "pending_short": ShortLeaveRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_missed": MissedPunchRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_od": OnDutyRequest.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "pending_halfday": HalfDayReview.objects.filter(employee=emp, status__in=OPEN_STATUSES).count(),
        "missing_punches": AttendanceRecord.objects.filter(employee=emp, status=AttendanceRecord.DayStatus.INCOMPLETE,
                                                           date__gte=today - datetime.timedelta(days=31)).order_by("-date")[:5],
        "recent_requests": registry.my_requests(emp)[:6],
        "open_queries": EmployeeQuery.objects.filter(employee=emp).exclude(status__in=["RESOLVED", "CLOSED"]).count(),
        "approvals_waiting": len(registry.pending_for(request.user)) if is_manager(request.user) else 0,
        "notifications": request.user.notifications.all()[:5],
    })
    return render(request, "dashboard/home.html", ctx)


@hr_required
def admin_dashboard(request):
    from apps.attendance.models import AttendanceRecord, HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
    from apps.employees.models import Department, Employee
    from apps.imports.models import AttendanceImportBatch
    from apps.leave_management.models import LeaveRequest
    from apps.queries.models import EmployeeQuery

    try:
        day = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    dept = request.GET.get("department") or ""
    emps = Employee.objects.filter(is_active=True)
    if dept:
        emps = emps.filter(department_id=dept)
    recs = AttendanceRecord.objects.filter(date=day, employee__in=emps)
    DS = AttendanceRecord.DayStatus
    by_status = dict(recs.values_list("status").annotate(n=Count("id")))
    on_leave = LeaveRequest.objects.filter(status=Status.APPROVED, start_date__lte=day, end_date__gte=day, employee__in=emps).count()

    def pending(model):
        return model.objects.filter(status__in=PENDING_STATUSES, employee__in=emps).count()

    latest_import = AttendanceImportBatch.objects.exclude(status=AttendanceImportBatch.Status.DISCARDED).first()
    trend = []
    for i in range(13, -1, -1):
        d = day - datetime.timedelta(days=i)
        r = AttendanceRecord.objects.filter(date=d, employee__in=emps)
        trend.append({"d": d.strftime("%d %b"), "present": r.filter(status__in=[DS.PRESENT, DS.HALF_DAY_FLAG, DS.ON_DUTY]).count(),
                      "absent": r.filter(status=DS.ABSENT).count(), "late": r.filter(late_minutes__gt=0).count()})
    has_data = recs.exclude(status=DS.NOT_IMPORTED).exists()
    ctx = {
        "day": day, "dept": dept, "departments": Department.objects.filter(is_active=True), "has_data": has_data,
        "active_employees": emps.count(),
        "present": by_status.get(DS.PRESENT, 0) + by_status.get(DS.HALF_DAY_FLAG, 0) + by_status.get(DS.ON_DUTY, 0),
        "absent": by_status.get(DS.ABSENT, 0),
        "on_leave": on_leave,
        "incomplete": by_status.get(DS.INCOMPLETE, 0),
        "late": recs.filter(late_minutes__gt=0).count(),
        "half_day_flags": recs.filter(half_day_flag=True).count(),
        "not_imported": by_status.get(DS.NOT_IMPORTED, 0),
        "pending_leave": pending(LeaveRequest), "pending_short": pending(ShortLeaveRequest), "pending_missed": pending(MissedPunchRequest),
        "pending_od": pending(OnDutyRequest), "pending_halfday": pending(HalfDayReview),
        "open_queries": EmployeeQuery.objects.exclude(status__in=["RESOLVED", "CLOSED"]).count(),
        "latest_import": latest_import, "trend": trend,
        "emergency_overdue": LeaveRequest.objects.filter(is_emergency=True, deadline_breached=True).exclude(status__in=[Status.CANCELLED, Status.REJECTED]).count(),
        "awaiting_hr": len(registry.pending_for(request.user)),
        "dept_breakdown": recs.values("employee__department__name").annotate(
            present=Count("id", filter=Q(status__in=[DS.PRESENT, DS.HALF_DAY_FLAG, DS.ON_DUTY])),
            absent=Count("id", filter=Q(status=DS.ABSENT)), late=Count("id", filter=Q(late_minutes__gt=0))).order_by("employee__department__name"),
    }
    return render(request, "dashboard/admin_dashboard.html", ctx)
