"""Role-scoped report definitions.

Each report declares the audience that may run it and a function returning
(columns, rows). Filters are applied server-side, and every query is
restricted to employees inside the caller's authorisation scope. Columns are
limited to what the report needs — no identity, bank or salary data.
"""
import calendar
import datetime
from dataclasses import dataclass
from typing import Callable

from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone

from apps.approvals import registry
from apps.approvals.models import OPEN_STATUSES, Status
from apps.attendance.models import AttendanceAdjustment, AttendanceRecord, HalfDayReview, MissedPunchRequest, ShortLeaveRequest
from apps.attendance.services import display_status
from apps.core import permissions as perms
from apps.employees.models import Department
from apps.imports.models import AttendanceImportRow
from apps.leave_management.models import LeaveLedgerEntry, LeaveRequest, LeaveType
from apps.leave_management.services import balance
from apps.queries.models import EmployeeQuery


@dataclass
class Report:
    key: str
    title: str
    audience: str  # "employee" | "manager" | "admin"
    description: str
    filters: tuple
    fn: Callable


def t(v):
    return timezone.localtime(v).strftime("%H:%M") if v else ""


def month_range(f):
    today = timezone.localdate()
    try:
        y, m = [int(x) for x in (f.get("month") or f"{today:%Y-%m}").split("-")]
        first = datetime.date(y, m, 1)
    except (ValueError, TypeError):
        first = today.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def date_range(f, default_days=30):
    today = timezone.localdate()
    try:
        start = datetime.date.fromisoformat(f.get("from") or "")
    except ValueError:
        start = today - datetime.timedelta(days=default_days)
    try:
        end = datetime.date.fromisoformat(f.get("to") or "")
    except ValueError:
        end = today
    if (end - start).days > 366:
        start = end - datetime.timedelta(days=366)
    return start, end


def year_of(f):
    try:
        return int(f.get("year") or timezone.localdate().year)
    except ValueError:
        return timezone.localdate().year


def scoped_employees(user, f, audience):
    """Employees the caller may report on for this audience, after filters."""
    if audience == "employee":
        if f.get("employee"):
            return perms.employee_scope(user).filter(pk=perms.get_scoped_employee(user, f["employee"]).pk)
        me = perms.own_employee(user)
        if not me:
            raise Http404
        return perms.employee_scope(user).filter(pk=me.pk)
    if audience == "manager":
        qs = perms.employee_scope(user) if perms.is_hr(user) and f.get("scope") == "company" else perms.team_queryset(user)
    else:
        qs = perms.employee_scope(user)
    if f.get("department"):
        qs = qs.filter(department_id=f["department"])
    if f.get("employee"):
        qs = qs.filter(pk=f["employee"])
    return qs.distinct()


# ---------------------------------------------------------------- employee
def r_my_attendance(user, f, emps):
    first, last = month_range(f)
    cols = ["Date", "Employee ID", "Shift", "In", "Out", "Worked (min)", "Late (min)", "Early (min)", "Grace used", "Month grace", "Status", "Explanation"]
    rows = [[r.date, r.employee.employee_id, f"{r.scheduled_start:%H:%M}-{r.scheduled_end:%H:%M}" if r.scheduled_start else "", t(r.first_in), t(r.last_out),
             r.worked_minutes or "", r.late_minutes, r.early_minutes, r.grace_minutes_used, r.month_grace_cumulative, display_status(r), r.explanation]
            for r in AttendanceRecord.objects.filter(employee__in=emps, date__gte=first, date__lte=last).select_related("employee").order_by("employee__employee_id", "date")]
    return cols, rows


def r_my_balance(user, f, emps):
    year = year_of(f)
    cols = ["Employee ID", "Name", "Leave type", "Opening", "Credited", "Used", "Adjustments", "Closing", "Pending", "Available"]
    rows = []
    for e in emps:
        for lt in LeaveType.objects.filter(is_active=True, tracks_balance=True):
            b = balance(e, lt, year)
            rows.append([e.employee_id, e.full_name, lt.name, b["opening"], b["credited"], b["used"], b["adjustments"], b["closing"], b["pending"], b["available"]])
    return cols, rows


def r_my_leave_history(user, f, emps):
    year = year_of(f)
    cols = ["Employee ID", "Request", "Type", "From", "To", "Days", "Emergency", "Status", "Submitted", "Decision comment"]
    rows = [[r.employee.employee_id, r.pk, r.leave_type.name, r.start_date, r.end_date, r.days_requested, "Yes" if r.is_emergency else "",
             r.get_status_display(), timezone.localtime(r.submitted_at).strftime("%Y-%m-%d") if r.submitted_at else "", r.decision_comment]
            for r in LeaveRequest.objects.filter(employee__in=emps, start_date__year=year).select_related("employee", "leave_type").order_by("start_date")]
    return cols, rows


def r_my_missed_punch(user, f, emps):
    start, end = date_range(f, 365)
    cols = ["Employee ID", "Date", "Issue", "Original In", "Original Out", "Requested In", "Requested Out", "Status", "Decision comment"]
    rows = [[r.employee.employee_id, r.date, r.get_issue_type_display(), t(r.original_in), t(r.original_out),
             r.requested_in.strftime("%H:%M") if r.requested_in else "", r.requested_out.strftime("%H:%M") if r.requested_out else "",
             r.get_status_display(), r.decision_comment]
            for r in MissedPunchRequest.objects.filter(employee__in=emps, date__gte=start, date__lte=end).select_related("employee").order_by("date")]
    return cols, rows


def r_my_grace(user, f, emps):
    first, last = month_range(f)
    cols = ["Employee ID", "Date", "In", "Late (min)", "Grace used", "Month cumulative", "Half-day flag", "Explanation"]
    rows = [[r.employee.employee_id, r.date, t(r.first_in), r.late_minutes, r.grace_minutes_used, r.month_grace_cumulative,
             "Yes" if r.half_day_flag else "", r.explanation]
            for r in AttendanceRecord.objects.filter(employee__in=emps, date__gte=first, date__lte=last, late_minutes__gt=0).select_related("employee").order_by("date")]
    return cols, rows


# ---------------------------------------------------------------- manager
def r_team_attendance(user, f, emps):
    start, end = date_range(f, 7)
    cols = ["Date", "Employee ID", "Name", "Department", "In", "Out", "Worked (min)", "Late (min)", "Status"]
    rows = [[r.date, r.employee.employee_id, r.employee.full_name, str(r.employee.department or ""), t(r.first_in), t(r.last_out),
             r.worked_minutes or "", r.late_minutes, display_status(r)]
            for r in AttendanceRecord.objects.filter(employee__in=emps, date__gte=start, date__lte=end).select_related("employee", "employee__department").order_by("date", "employee__employee_id")]
    return cols, rows


def r_team_leave(user, f, emps):
    start, end = date_range(f, 0)
    end = max(end, start + datetime.timedelta(days=30))
    cols = ["Employee ID", "Name", "From", "To", "Days", "Status"]
    rows = [[r.employee.employee_id, r.employee.full_name, r.start_date, r.end_date, r.days_requested, r.get_status_display()]
            for r in LeaveRequest.objects.filter(employee__in=emps, start_date__lte=end, end_date__gte=start, status__in=list(OPEN_STATUSES) + [Status.APPROVED])
            .select_related("employee").order_by("start_date")]
    return cols, rows


def r_pending(user, f, emps):
    cols = ["Type", "Request", "Employee ID", "Name", "Summary", "Status", "Submitted"]
    rows = [[r.TYPE_LABEL, r.pk, r.employee.employee_id, r.employee.full_name, r.summary(), r.get_status_display(),
             timezone.localtime(r.submitted_at).strftime("%Y-%m-%d %H:%M") if r.submitted_at else ""]
            for r in registry.pending_for(user) if r.employee_id in set(emps.values_list("pk", flat=True))]
    return cols, rows


def r_team_exceptions(user, f, emps):
    start, end = date_range(f, 30)
    cols = ["Date", "Employee ID", "Name", "Status", "Late (min)", "Half-day flag", "Explanation"]
    qs = AttendanceRecord.objects.filter(employee__in=emps, date__gte=start, date__lte=end).filter(
        Q(status__in=["ABSENT", "INCOMPLETE"]) | Q(half_day_flag=True) | Q(late_minutes__gt=0) | Q(early_departure_flag=True))
    rows = [[r.date, r.employee.employee_id, r.employee.full_name, display_status(r), r.late_minutes, "Yes" if r.half_day_flag else "", r.explanation]
            for r in qs.select_related("employee").order_by("date")]
    return cols, rows


def r_decided(user, f, emps):
    start, end = date_range(f, 30)
    ids = set(emps.values_list("pk", flat=True))
    cols = ["Type", "Request", "Employee ID", "Summary", "Decision", "Decided at", "Decided by", "Comment"]
    rows = []
    for slug, model in registry.request_models().items():
        for r in model.objects.filter(employee_id__in=ids, status__in=[Status.APPROVED, Status.REJECTED], decided_at__date__gte=start,
                                      decided_at__date__lte=end).select_related("employee", "decided_by"):
            rows.append([r.TYPE_LABEL, r.pk, r.employee.employee_id, r.summary(), r.get_status_display(),
                         timezone.localtime(r.decided_at).strftime("%Y-%m-%d %H:%M"), r.decided_by.employee_code if r.decided_by else "", r.decision_comment])
    return cols, rows


# ---------------------------------------------------------------- admin
def r_attendance_summary(user, f, emps):
    first, last = month_range(f)
    cols = ["Employee ID", "Name", "Department", "Present", "Half-day flags", "Absent", "Missing punch", "Leave", "On duty", "Late days", "Grace used (min)", "Not imported"]
    agg = AttendanceRecord.objects.filter(employee__in=emps, date__gte=first, date__lte=last).values("employee_id").annotate(
        present=Count("id", filter=Q(status="PRESENT")), flags=Count("id", filter=Q(half_day_flag=True)), absent=Count("id", filter=Q(status="ABSENT")),
        inc=Count("id", filter=Q(status="INCOMPLETE")), leave=Count("id", filter=Q(status__in=["LEAVE", "HALF_DAY_LEAVE"])),
        od=Count("id", filter=Q(status="ON_DUTY")), late=Count("id", filter=Q(late_minutes__gt=0)), grace=Sum("grace_minutes_used"),
        ni=Count("id", filter=Q(status="NOT_IMPORTED")))
    by = {a["employee_id"]: a for a in agg}
    rows = []
    for e in emps.select_related("department").order_by("employee_id"):
        a = by.get(e.pk, {})
        rows.append([e.employee_id, e.full_name, str(e.department or ""), a.get("present", 0), a.get("flags", 0), a.get("absent", 0), a.get("inc", 0),
                     a.get("leave", 0), a.get("od", 0), a.get("late", 0), a.get("grace") or 0, a.get("ni", 0)])
    return cols, rows


def r_late(user, f, emps):
    first, last = month_range(f)
    cols = ["Date", "Employee ID", "Name", "In", "Late (min)", "Grace used", "Month cumulative", "Flag", "Explanation"]
    rows = [[r.date, r.employee.employee_id, r.employee.full_name, t(r.first_in), r.late_minutes, r.grace_minutes_used, r.month_grace_cumulative,
             r.get_flag_code_display() if r.flag_code else "", r.explanation]
            for r in AttendanceRecord.objects.filter(employee__in=emps, date__gte=first, date__lte=last, late_minutes__gt=0).select_related("employee").order_by("date", "employee__employee_id")]
    return cols, rows


def r_grace_util(user, f, emps):
    first, last = month_range(f)
    cols = ["Employee ID", "Name", "Late days", "Grace used (min)", "Monthly limit", "Remaining", "Flags after limit"]
    from apps.attendance.services import monthly_grace_summary

    rows = []
    for e in emps.order_by("employee_id"):
        g = monthly_grace_summary(e, first.year, first.month)
        rows.append([e.employee_id, e.full_name, g["late_days"], g["used"], g["limit"] or "", g["remaining"] if g["remaining"] is not None else "", g["late_after_limit"]])
    return cols, rows


def r_short_leave(user, f, emps):
    first, last = month_range(f)
    cols = ["Employee ID", "Name", "Date", "Type", "From", "To", "Minutes", "Status", "Team notified"]
    rows = [[r.employee.employee_id, r.employee.full_name, r.date, r.get_kind_display(), r.start_time.strftime("%H:%M"), r.end_time.strftime("%H:%M"),
             r.duration_minutes, r.get_status_display(), "Yes" if r.team_notified else ""]
            for r in ShortLeaveRequest.objects.filter(employee__in=emps, date__gte=first, date__lte=last).select_related("employee").order_by("date")]
    return cols, rows


def r_half_day(user, f, emps):
    first, last = month_range(f)
    cols = ["Date", "Employee ID", "Name", "Flag", "Review status", "Outcome", "Explanation"]
    rows = [[rv.attendance_record.date, rv.employee.employee_id, rv.employee.full_name, rv.attendance_record.get_flag_code_display(),
             rv.get_status_display(), rv.get_outcome_display() if rv.outcome else "Pending", rv.flag_explanation]
            for rv in HalfDayReview.objects.filter(employee__in=emps, attendance_record__date__gte=first, attendance_record__date__lte=last)
            .select_related("employee", "attendance_record").order_by("attendance_record__date")]
    return cols, rows


def r_corrections(user, f, emps):
    start, end = date_range(f, 30)
    cols = ["Date", "Employee ID", "Source", "Original In", "Original Out", "Corrected In", "Corrected Out", "Approved by", "Active", "Reason"]
    rows = [[a.date, a.employee.employee_id, a.get_source_display(), t(a.original_in), t(a.original_out), t(a.corrected_in), t(a.corrected_out),
             a.approved_by.employee_code if a.approved_by else "", "Yes" if a.is_active else "Superseded", a.reason]
            for a in AttendanceAdjustment.objects.filter(employee__in=emps, date__gte=start, date__lte=end).select_related("employee", "approved_by").order_by("date")]
    return cols, rows


def r_ledger(user, f, emps):
    year = year_of(f)
    cols = ["Employee ID", "Leave type", "Effective", "Entry", "Quantity", "Reason", "Request", "Posted by", "Posted at"]
    rows = [[e.employee.employee_id, e.leave_type.code, e.effective_date, e.get_entry_type_display(), e.quantity, e.reason, e.leave_request_id or "",
             e.created_by.employee_code if e.created_by else "system", timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M")]
            for e in LeaveLedgerEntry.objects.filter(employee__in=emps, leave_year=year).select_related("employee", "leave_type", "created_by").order_by("employee__employee_id", "effective_date", "id")]
    return cols, rows


def r_utilization(user, f, emps):
    year = year_of(f)
    cols = ["Department", "Leave type", "Approved requests", "Days approved", "Pending days"]
    rows = []
    for d in Department.objects.all().order_by("name"):
        for lt in LeaveType.objects.filter(is_active=True):
            qs = LeaveRequest.objects.filter(employee__in=emps.filter(department=d), leave_type=lt, start_date__year=year)
            appr = qs.filter(status=Status.APPROVED)
            n = appr.count()
            pend = qs.filter(status__in=OPEN_STATUSES).aggregate(s=Sum("days_requested"))["s"] or 0
            if n or pend:
                rows.append([d.name, lt.name, n, appr.aggregate(s=Sum("days_requested"))["s"] or 0, pend])
    return cols, rows


def r_queries(user, f, emps):
    start, end = date_range(f, 90)
    cols = ["Ref", "Employee ID", "Category", "Subject", "Status", "Assigned to", "Created", "Resolved"]
    rows = [[q.reference, q.employee.employee_id, q.get_category_display(), q.subject, q.get_status_display(),
             q.assigned_to.employee_code if q.assigned_to else "", timezone.localtime(q.created_at).strftime("%Y-%m-%d"),
             timezone.localtime(q.resolved_at).strftime("%Y-%m-%d") if q.resolved_at else ""]
            for q in EmployeeQuery.objects.filter(employee__in=emps, created_at__date__gte=start, created_at__date__lte=end).select_related("employee", "assigned_to")]
    return cols, rows


def r_import_recon(user, f, emps):
    start, end = date_range(f, 90)
    cols = ["Batch", "File", "Row", "Employee ID", "Date", "Status", "Reconciled by", "Messages"]
    rows = [[r.batch.batch_code, r.batch.original_name, r.row_number, r.employee_code, r.attendance_date or "", r.get_status_display(),
             r.reconciled_by.employee_code if r.reconciled_by else "", r.message_text]
            for r in AttendanceImportRow.objects.filter(batch__created_at__date__gte=start, batch__created_at__date__lte=end,
                                                        status__in=["CONFLICT", "RECON_OK", "RECON_NO", "ERROR"]).select_related("batch", "reconciled_by")
            .order_by("batch_id", "row_number")[:20000]]
    return cols, rows


def r_payroll_exceptions(user, f, emps):
    first, last = month_range(f)
    cols = ["Employee ID", "Name", "Date(s)", "Category", "Detail", "Status"]
    rows = []
    for r in LeaveRequest.objects.filter(employee__in=emps, start_date__lte=last, end_date__gte=first).filter(
            Q(payroll_review_required=True) | Q(leave_type__is_paid=False) | Q(deadline_breached=True)).exclude(status__in=[Status.CANCELLED, Status.REJECTED]).select_related("employee", "leave_type"):
        cat = "Emergency form overdue" if r.deadline_breached else ("Unpaid leave type" if not r.leave_type.is_paid else "Flagged for review")
        rows.append([r.employee.employee_id, r.employee.full_name, f"{r.start_date} – {r.end_date}", cat, f"{r.leave_type.name}, {r.days_requested} day(s)", r.get_status_display()])
    for rv in HalfDayReview.objects.filter(employee__in=emps, outcome=HalfDayReview.Outcome.CONFIRMED, attendance_record__date__gte=first,
                                           attendance_record__date__lte=last).select_related("employee", "attendance_record"):
        rows.append([rv.employee.employee_id, rv.employee.full_name, str(rv.attendance_record.date), "Half day confirmed", rv.flag_explanation[:120], "Confirmed"])
    for rec in AttendanceRecord.objects.filter(employee__in=emps, date__gte=first, date__lte=last, status__in=["ABSENT", "INCOMPLETE"]).select_related("employee"):
        rows.append([rec.employee.employee_id, rec.employee.full_name, str(rec.date), rec.get_status_display(), rec.explanation[:120], "Unresolved"])
    rows.sort(key=lambda r: (r[0], r[2]))
    return cols, rows


REPORTS = {r.key: r for r in [
    Report("my-attendance", "Monthly attendance", "employee", "Daily attendance with calculation explanations.", ("month",), r_my_attendance),
    Report("my-leave-balance", "Leave balance", "employee", "Balance per leave type from the ledger.", ("year",), r_my_balance),
    Report("my-leave-history", "Leave history", "employee", "All leave requests in a year.", ("year",), r_my_leave_history),
    Report("my-missed-punch", "Missed-punch history", "employee", "Missed-punch requests and outcomes.", ("from", "to"), r_my_missed_punch),
    Report("my-grace", "Grace-period summary", "employee", "Late arrivals and grace usage for a month.", ("month",), r_my_grace),
    Report("team-attendance", "Team attendance", "manager", "Daily attendance for your team.", ("from", "to", "department"), r_team_attendance),
    Report("team-leave", "Team leave calendar", "manager", "Approved and pending leave.", ("from", "to", "department"), r_team_leave),
    Report("pending-approvals", "Pending approvals", "manager", "Requests awaiting your decision.", (), r_pending),
    Report("team-exceptions", "Team attendance exceptions", "manager", "Absent, missing punch, late, half-day and early-departure days.", ("from", "to", "department"), r_team_exceptions),
    Report("decided-requests", "Approved & rejected requests", "manager", "Decisions in a date range.", ("from", "to", "department"), r_decided),
    Report("attendance-summary", "Company attendance summary", "admin", "Per-employee monthly totals.", ("month", "department"), r_attendance_summary),
    Report("late-arrivals", "Monthly late-arrival report", "admin", "Every late arrival with grace calculation.", ("month", "department"), r_late),
    Report("grace-utilization", "Grace-period utilisation", "admin", "Monthly grace used vs. limit per employee.", ("month", "department"), r_grace_util),
    Report("short-leave-usage", "Short-leave usage", "admin", "Short leaves in a month.", ("month", "department"), r_short_leave),
    Report("half-day-flags", "Half-day flags", "admin", "Flags and review outcomes.", ("month", "department"), r_half_day),
    Report("missed-punch-corrections", "Missed-punch corrections", "admin", "All attendance adjustments with original values.", ("from", "to", "department"), r_corrections),
    Report("leave-balances", "Leave balances", "admin", "Balances for all employees.", ("year", "department"), r_my_balance),
    Report("leave-ledger", "Leave ledger", "admin", "Every ledger entry for the year.", ("year", "department"), r_ledger),
    Report("leave-utilization", "Leave utilisation", "admin", "Approved and pending days by department and type.", ("year", "department"), r_utilization),
    Report("employee-queries", "Employee queries", "admin", "Helpdesk queries and resolution.", ("from", "to", "department"), r_queries),
    Report("import-reconciliation", "Attendance import reconciliation", "admin", "Rejected and conflicting import rows.", ("from", "to"), r_import_recon),
    Report("payroll-exceptions", "Payroll-related leave exceptions", "admin", "Leave days needing payroll review (no salary is calculated).", ("month", "department"), r_payroll_exceptions),
]}


def allowed(user, report):
    if report.audience == "employee":
        return True
    if report.audience == "manager":
        return perms.is_manager(user)
    return perms.is_hr(user)
