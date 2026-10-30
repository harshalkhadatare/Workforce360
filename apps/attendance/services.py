"""Attendance policy engine (Attendance Policy No. 002 R2).

`recalculate_month` rebuilds AttendanceRecord rows for one employee/month from
punches, adjustments and approved requests. It is deterministic, so running it
twice produces the same result. Monthly grace is cumulative, so the whole
month is always recomputed in date order.

Every half-day flag gets a plain-language explanation stored on the record.
"""
import calendar
import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.approvals.models import Status
from apps.policies.models import PolicyVersion
from apps.policies.services import PolicyNotConfigured, get_policy, get_schedule, holidays_between

from .models import AttendanceAdjustment, AttendancePunch, AttendanceRecord, HalfDayReview, OnDutyRequest, ShortLeaveRequest

DS = AttendanceRecord.DayStatus
FC = AttendanceRecord.FlagCode


def local_tz():
    return timezone.get_current_timezone()


def combine(d, t):
    return timezone.make_aware(datetime.datetime.combine(d, t), local_tz())


def minutes_between(a, b):
    """Whole minutes from a to b (seconds truncated)."""
    return int((b - a).total_seconds() // 60)


def parse_hhmm(value):
    if isinstance(value, datetime.time):
        return value
    h, m = str(value).split(":")[:2]
    return datetime.time(int(h), int(m))


# ---------------------------------------------------------------------------
# Pure grace-period calculation — unit tested independently of the database.
# ---------------------------------------------------------------------------
@dataclass
class LateDay:
    date: datetime.date
    arrival: datetime.time
    late_minutes: int  # minutes after scheduled start (0 if on time)
    scheduled_start: datetime.time


@dataclass
class GraceResult:
    grace_used: int
    cumulative_after: int
    half_day: bool
    flag_code: str
    explanation: str


def evaluate_grace_month(days, *, daily_max, monthly_max, cutoff, interpretation):
    """Apply the grace rules to an ordered list of LateDay for ONE month.

    Rules (Attendance Policy 002 R2, as summarised in the brief):
      * up to `daily_max` minutes on a single day;
      * at most `monthly_max` cumulative minutes per month;
      * arrival after `cutoff` → half-day rule;
      * late arrivals after the monthly limit is exhausted → half-day rule.

    `interpretation` resolves the unclear phrase "effective from the first
    occurrence" (AWAITING HR CONFIRMATION):
      PROSPECTIVE   — only the arrival that would exceed the limit, and later ones, are flagged.
      RETROSPECTIVE — if the month's within-grace late minutes exceed the limit, every
                      late arrival in the month (from the first occurrence) is flagged.
    """
    results = {}
    cumulative = 0
    for d in days:
        if d.late_minutes <= 0:
            results[d.date] = GraceResult(0, cumulative, False, "", "On time.")
            continue
        arr = d.arrival.strftime("%H:%M")
        if d.arrival > cutoff:
            results[d.date] = GraceResult(0, cumulative, True, FC.AFTER_CUTOFF,
                f"Arrived {arr}, after the {cutoff:%H:%M} cut-off. Arrival after {cutoff:%H:%M} is subject to the half-day rule; grace does not apply.")
            continue
        if d.late_minutes > daily_max:
            results[d.date] = GraceResult(0, cumulative, True, FC.EXCEEDS_DAILY,
                f"Arrived {arr}, {d.late_minutes} min late — more than the {daily_max}-minute daily grace.")
            continue
        if interpretation == "PROSPECTIVE" and cumulative + d.late_minutes > monthly_max:
            remaining = max(monthly_max - cumulative, 0)
            results[d.date] = GraceResult(0, cumulative, True, FC.EXCEEDS_MONTHLY,
                f"Arrived {arr}, {d.late_minutes} min late. Monthly grace already used: {cumulative} of {monthly_max} min "
                f"({remaining} min left), so this arrival exceeds the monthly limit and is subject to the half-day rule. "
                f"[Interpretation: prospective — awaiting HR confirmation]")
            continue
        cumulative += d.late_minutes
        results[d.date] = GraceResult(d.late_minutes, cumulative, False, "",
            f"Arrived {arr}, {d.late_minutes} min late — covered by grace. Month total: {cumulative} of {monthly_max} min.")

    if interpretation == "RETROSPECTIVE" and cumulative > monthly_max:
        for d in days:
            r = results[d.date]
            if d.late_minutes > 0 and not r.half_day:
                results[d.date] = GraceResult(0, r.cumulative_after, True, FC.MONTHLY_RETRO,
                    f"Arrived {d.arrival:%H:%M}, {d.late_minutes} min late. Late minutes in the month total {cumulative}, "
                    f"exceeding the {monthly_max}-minute limit; under the retrospective reading every late arrival from the "
                    f"first occurrence is subject to the half-day rule. [Interpretation: retrospective — awaiting HR confirmation]")
    return results


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------
def _approved_leaves(employee, start, end):
    from apps.leave_management.models import LeaveRequest

    out = {}
    qs = LeaveRequest.objects.filter(employee=employee, status=Status.APPROVED, start_date__lte=end, end_date__gte=start)
    for lr in qs:
        d = max(lr.start_date, start)
        while d <= min(lr.end_date, end):
            out[d] = lr
            d += datetime.timedelta(days=1)
    return out


def _approved_on_duty(employee, start, end):
    out = {}
    s = combine(start, datetime.time.min)
    e = combine(end, datetime.time.max)
    for od in OnDutyRequest.objects.filter(employee=employee, status=Status.APPROVED, start_at__lte=e, end_at__gte=s):
        for d in od.dates():
            out.setdefault(d, []).append(od)
    return out


def _approved_short_leaves(employee, start, end):
    out = {}
    for sl in ShortLeaveRequest.objects.filter(employee=employee, status=Status.APPROVED, date__gte=start, date__lte=end):
        out.setdefault(sl.date, []).append(sl)
    return out


def import_covered_dates(start, end):
    """Dates covered by any completed import batch."""
    from apps.imports.models import AttendanceImportBatch

    covered = set()
    batches = AttendanceImportBatch.objects.filter(
        status__in=[AttendanceImportBatch.Status.COMPLETED, AttendanceImportBatch.Status.COMPLETED_WITH_ERRORS],
        date_from__lte=end, date_to__gte=start,
    ).values_list("date_from", "date_to")
    for f, t in batches:
        d = max(f, start)
        while d <= min(t, end):
            covered.add(d)
            d += datetime.timedelta(days=1)
    return covered


def effective_punches(employee, d, punches=None, adjustment=None):
    """Return (first_in, last_out, count, original_in, original_out)."""
    if punches is None:
        punches = list(AttendancePunch.objects.filter(employee=employee, punch_date=d, is_void=False).order_by("punch_time"))
    ins = [p.punch_time for p in punches if p.punch_type == "IN"]
    outs = [p.punch_time for p in punches if p.punch_type == "OUT"]
    unknown = [p.punch_time for p in punches if p.punch_type == "UNKNOWN"]
    first_in = min(ins) if ins else None
    last_out = max(outs) if outs else None
    if unknown:
        allp = sorted(unknown + ins + outs)
        if first_in is None:
            first_in = allp[0]
        if last_out is None and len(allp) >= 2:
            last_out = allp[-1]
    if first_in and last_out and last_out <= first_in:
        last_out = None  # an "out" before the first "in" is treated as missing
    orig_in, orig_out = first_in, last_out
    if adjustment is not None:
        if adjustment.corrected_in:
            first_in = adjustment.corrected_in
        if adjustment.corrected_out:
            last_out = adjustment.corrected_out
    return first_in, last_out, len(punches), orig_in, orig_out


# ---------------------------------------------------------------------------
# Month recalculation
# ---------------------------------------------------------------------------
@transaction.atomic
def recalculate_month(employee, year, month, today=None):
    today = today or timezone.localdate()
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    end = min(last, today)
    if end < first or employee.date_of_joining > end:
        return []
    start = max(first, employee.date_of_joining)

    punches_by_day = {}
    for p in AttendancePunch.objects.filter(employee=employee, punch_date__gte=start, punch_date__lte=end, is_void=False).order_by("punch_time"):
        punches_by_day.setdefault(p.punch_date, []).append(p)
    adjustments = {}
    for a in AttendanceAdjustment.objects.filter(employee=employee, date__gte=start, date__lte=end, is_active=True).order_by("created_at"):
        adjustments[a.date] = a  # latest active wins
    leaves = _approved_leaves(employee, start, end)
    on_duty = _approved_on_duty(employee, start, end)
    short_leaves = _approved_short_leaves(employee, start, end)
    holidays = holidays_between(start, end, employee.work_location)
    covered = import_covered_dates(start, end)

    day_info = []
    late_days = []
    d = start
    while d <= end:
        try:
            sched = get_schedule(employee, d)
            policy = get_policy(PolicyVersion.Kind.ATTENDANCE, d, employee)
        except PolicyNotConfigured as exc:
            day_info.append({"date": d, "skip": str(exc)})
            d += datetime.timedelta(days=1)
            continue
        adj = adjustments.get(d)
        first_in, last_out, count, _, _ = effective_punches(employee, d, punches_by_day.get(d, []), adj)
        info = {"date": d, "sched": sched, "policy": policy, "first_in": first_in, "last_out": last_out,
                "count": count, "adjusted": adj is not None, "leave": leaves.get(d), "od": on_duty.get(d, []),
                "sl": short_leaves.get(d, []), "holiday": holidays.get(d), "weekly_off": d.weekday() in (sched.weekly_off_days or []),
                "covered": d in covered or count > 0 or adj is not None}
        working = not info["holiday"] and not info["weekly_off"]
        leave = info["leave"]
        full_leave = leave is not None and not leave.is_half_day
        first_half_leave = leave is not None and leave.is_half_day and leave.half_day_session == "FIRST"
        od_covers_start = any(od.start_at <= combine(d, sched.start_time) for od in info["od"])
        if working and first_in and not full_leave and not first_half_leave and not od_covers_start:
            late = max(0, minutes_between(combine(d, sched.start_time), first_in))
            if policy.get("short_leave_grace_interaction") == "OFFSET_LATE":
                for sl in info["sl"]:
                    if sl.kind == ShortLeaveRequest.Kind.LATE_ARRIVAL:
                        late = max(0, late - sl.duration_minutes)
            info["late"] = late
            # Minute granularity: seconds are ignored for both late minutes and the cut-off comparison.
            late_days.append(LateDay(d, timezone.localtime(first_in).time().replace(second=0, microsecond=0), late, sched.start_time))
        else:
            info["late"] = 0
        day_info.append(info)
        d += datetime.timedelta(days=1)

    # Policy parameters for the grace evaluation: use the version effective on the
    # LAST evaluated day of the month (a mid-month policy change is rare; the version
    # used is stored on every record for reproducibility).
    evaluated = [i for i in day_info if "skip" not in i]
    if not evaluated:
        return []
    month_policy = evaluated[-1]["policy"]
    grace = evaluate_grace_month(
        late_days,
        daily_max=int(month_policy.get("grace_daily_max_minutes")),
        monthly_max=int(month_policy.get("grace_monthly_max_minutes")),
        cutoff=parse_hhmm(month_policy.get("half_day_cutoff_time")),
        interpretation=month_policy.get("grace_monthly_interpretation"),
    )

    records = []
    running = 0
    for info in evaluated:
        records.append(_save_day(employee, info, grace.get(info["date"]), month_policy, running))
        g = grace.get(info["date"])
        if g:
            running = g.cumulative_after
    for rec in records:
        _sync_half_day_review(rec)
    return records


def _save_day(employee, info, g, policy, running):
    d, sched = info["date"], info["sched"]
    first_in, last_out = info["first_in"], info["last_out"]
    leave = info["leave"]
    working = not info["holiday"] and not info["weekly_off"]
    notes = []
    half_day, flag_code, explanation = False, "", ""
    late = early = grace_used = 0
    worked = minutes_between(first_in, last_out) if first_in and last_out else None
    early_flag = False

    if info["holiday"]:
        status = DS.HOLIDAY
        notes.append(info["holiday"].name)
        if info["count"]:
            notes.append("Punches recorded on a holiday")
    elif info["weekly_off"]:
        status = DS.WEEKLY_OFF
        if info["count"]:
            notes.append("Punches recorded on a weekly off")
    elif leave is not None and not leave.is_half_day:
        status = DS.LEAVE
        notes.append(leave.leave_type.name)
    elif not first_in and not last_out:
        if info["od"]:
            status = DS.ON_DUTY
        elif info["covered"]:
            status = DS.ABSENT
            explanation = "No punches were recorded for this working day in the imported data, and no approved leave or on-duty covers it."
        else:
            status = DS.NOT_IMPORTED
            explanation = "Attendance data for this date has not been imported yet."
    elif not first_in or not last_out:
        status = DS.ON_DUTY if info["od"] else DS.INCOMPLETE
        missing = "In" if not first_in else "Out"
        explanation = f"Missing {missing} punch. This is not treated as absence; submit a missed-punch request or wait for review."
    else:
        status = DS.HALF_DAY_LEAVE if leave is not None else (DS.ON_DUTY if info["od"] and not first_in else DS.PRESENT)

    if working and first_in and status not in (DS.LEAVE,):
        late = info["late"]
        if g is not None and late > 0:
            grace_used, explanation_g = g.grace_used, g.explanation
            if g.half_day:
                half_day, flag_code = True, g.flag_code
                if status == DS.PRESENT:
                    status = DS.HALF_DAY_FLAG
            explanation = (explanation + " " + explanation_g).strip()
    if working and last_out and status not in (DS.LEAVE,):
        sched_end = combine(d, sched.end_time)
        second_half_leave = leave is not None and leave.is_half_day and leave.half_day_session == "SECOND"
        covered_by_sl = any(s.kind == ShortLeaveRequest.Kind.EARLY_DEPARTURE for s in info["sl"])
        covered_by_od = any(od.end_at >= sched_end for od in info["od"])
        if last_out < sched_end and not second_half_leave:
            early = minutes_between(last_out, sched_end)
            if not covered_by_sl and not covered_by_od:
                early_flag = True
                notes.append(f"Left {early} min before shift end (recorded for review; policy does not define a penalty)")
            else:
                notes.append("Early departure covered by approved short leave / on-duty")
    for sl in info["sl"]:
        notes.append(f"Short leave {sl.start_time:%H:%M}–{sl.end_time:%H:%M}")
    if info["od"]:
        notes.append("On duty: " + ", ".join(od.destination for od in info["od"]))
    if info["adjusted"]:
        notes.append("Corrected by approved adjustment")

    rec, _ = AttendanceRecord.objects.update_or_create(
        employee=employee, date=d,
        defaults=dict(
            schedule=sched, scheduled_start=sched.start_time, scheduled_end=sched.end_time,
            first_in=first_in, last_out=last_out, punch_count=info["count"], worked_minutes=worked,
            late_minutes=late, early_minutes=early, grace_minutes_used=grace_used,
            month_grace_cumulative=(g.cumulative_after if g else running),
            status=status, half_day_flag=half_day, flag_code=flag_code, explanation=explanation.strip(),
            early_departure_flag=early_flag, is_adjusted=info["adjusted"], policy_version=info["policy"],
            notes="; ".join(notes)[:255],
        ),
    )
    return rec


def _sync_half_day_review(rec):
    """Create a review for new flags; cancel open reviews whose flag disappeared.

    Decided reviews (waived / confirmed) are never altered by a recalculation."""
    from apps.approvals import services as wf

    live = rec.half_day_reviews.exclude(status=Status.CANCELLED).first()
    if rec.half_day_flag and live is None:
        review = HalfDayReview(employee=rec.employee, attendance_record=rec, origin=HalfDayReview.Origin.AUTO_FLAG,
                               flag_explanation=rec.explanation)
        wf.submit(review, None)
    elif not rec.half_day_flag and live is not None and live.is_open:
        live.status = Status.CANCELLED
        live.decision_comment = "Flag no longer applies after recalculation."
        live.save(update_fields=["status", "decision_comment", "updated_at"])
        wf._record(live, None, "SYSTEM", "PENDING", "Flag cleared by recalculation (e.g. approved correction).")


def recalculate_for_dates(employee, dates):
    months = sorted({(d.year, d.month) for d in dates})
    for y, m in months:
        recalculate_month(employee, y, m)


def display_status(rec):
    """Status label taking half-day review decisions into account."""
    if rec.half_day_flag:
        review = rec.open_review
        if review and review.outcome == HalfDayReview.Outcome.WAIVED:
            return "Present (flag waived)"
        if review and review.outcome == HalfDayReview.Outcome.CONFIRMED:
            return "Half day (confirmed)"
        if review and review.status == Status.PENDING_HR:
            return "Half day — awaiting HR review"
        return "Half day — pending review"
    return rec.get_status_display()


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------
def _requested_dt(d, t):
    return combine(d, t) if t else None


def apply_missed_punch(req, actor):
    """Create the audited adjustment for an approved missed-punch request (idempotent)."""
    from apps.audit import services as audit

    if AttendanceAdjustment.objects.filter(missed_punch_request=req).exists():
        return
    first_in, last_out, _, orig_in, orig_out = effective_punches(req.employee, req.date)
    corrected_in = _requested_dt(req.date, req.requested_in)
    corrected_out = _requested_dt(req.date, req.requested_out)
    if not corrected_in and not corrected_out:
        return  # e.g. "incorrect status" — reviewed, nothing to change in punches
    AttendanceAdjustment.objects.filter(employee=req.employee, date=req.date, is_active=True).update(is_active=False)
    adj = AttendanceAdjustment.objects.create(
        employee=req.employee, date=req.date, original_in=orig_in, original_out=orig_out,
        corrected_in=corrected_in, corrected_out=corrected_out, reason=req.reason,
        source=AttendanceAdjustment.Source.MISSED_PUNCH, missed_punch_request=req, approved_by=actor,
    )
    audit.log(actor, "attendance.adjusted", adj, employee_ref=req.employee.employee_id,
              before={"in": orig_in, "out": orig_out}, after={"in": corrected_in, "out": corrected_out},
              reason=f"Missed-punch request #{req.pk}")
    recalculate_for_dates(req.employee, [req.date])


@transaction.atomic
def admin_correction(employee, d, corrected_in, corrected_out, reason, actor):
    from apps.audit import services as audit

    if not reason.strip():
        raise ValueError("A reason is required")
    _, _, _, orig_in, orig_out = effective_punches(employee, d)
    AttendanceAdjustment.objects.filter(employee=employee, date=d, is_active=True).update(is_active=False)
    adj = AttendanceAdjustment.objects.create(
        employee=employee, date=d, original_in=orig_in, original_out=orig_out,
        corrected_in=_requested_dt(d, corrected_in), corrected_out=_requested_dt(d, corrected_out),
        reason=reason, source=AttendanceAdjustment.Source.ADMIN_CORRECTION, approved_by=actor,
    )
    audit.log(actor, "attendance.admin_corrected", adj, employee_ref=employee.employee_id,
              before={"in": orig_in, "out": orig_out}, after={"in": adj.corrected_in, "out": adj.corrected_out}, reason=reason)
    recalculate_for_dates(employee, [d])
    return adj


def monthly_grace_summary(employee, year, month):
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    recs = AttendanceRecord.objects.filter(employee=employee, date__gte=first, date__lte=last)
    used = sum(r.grace_minutes_used for r in recs)
    try:
        limit = int(get_policy(PolicyVersion.Kind.ATTENDANCE, min(last, timezone.localdate()), employee).get("grace_monthly_max_minutes"))
    except PolicyNotConfigured:
        limit = None
    return {
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0) if limit is not None else None,
        "late_days": recs.filter(late_minutes__gt=0).count(),
        "half_day_flags": recs.filter(half_day_flag=True).count(),
        "late_after_limit": recs.filter(flag_code__in=[FC.EXCEEDS_MONTHLY, FC.MONTHLY_RETRO]).count(),
    }
