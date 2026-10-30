"""Server-side validation and submission for attendance-related requests."""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.approvals import services as wf
from apps.approvals.models import OPEN_STATUSES, Status
from apps.policies.models import PolicyVersion
from apps.policies.services import PolicyNotConfigured, get_policy, get_schedule

from .models import AttendanceRecord, HalfDayReview, MissedPunchRequest, OnDutyRequest, ShortLeaveRequest
from .services import combine, effective_punches


def _policy(employee, d):
    try:
        return get_policy(PolicyVersion.Kind.ATTENDANCE, d, employee)
    except PolicyNotConfigured as exc:
        raise ValidationError(str(exc))


@transaction.atomic
def submit_short_leave(employee, actor, *, date, kind, start_time, end_time, reason, attachment=None, now=None):
    now = now or timezone.now()
    if not (reason or "").strip():
        raise ValidationError({"reason": "A valid reason is required."})
    if end_time <= start_time:
        raise ValidationError({"end_time": "End time must be after start time."})
    policy = _policy(employee, date)
    duration = int((datetime.datetime.combine(date, end_time) - datetime.datetime.combine(date, start_time)).total_seconds() // 60)
    max_minutes = int(policy.get("short_leave_max_minutes"))
    if duration > max_minutes:
        raise ValidationError({"end_time": f"Short leave cannot exceed {max_minutes // 60} hours ({max_minutes} minutes). Requested: {duration} minutes."})

    # Monthly limit — count every request that is not rejected or cancelled.
    # Lock the employee row so two simultaneous submissions cannot both pass.
    type(employee).objects.select_for_update().get(pk=employee.pk)
    used = ShortLeaveRequest.objects.filter(employee=employee, date__year=date.year, date__month=date.month).exclude(
        status__in=[Status.REJECTED, Status.CANCELLED]).count()
    limit = int(policy.get("short_leave_max_per_month"))
    if used >= limit:
        raise ValidationError(f"You have already used {used} of {limit} short leaves allowed in {date:%B %Y}.")

    # Timing rule: before the leave is taken, or by the end of the same working day for late arrival
    start_dt = combine(date, start_time)
    if kind == ShortLeaveRequest.Kind.EARLY_DEPARTURE:
        if now >= start_dt:
            raise ValidationError("Early-departure short leave must be requested before you leave.")
    else:
        if policy.get("short_leave_late_deadline") == "END_OF_SHIFT":
            deadline = combine(date, get_schedule(employee, date).end_time)
        else:
            deadline = combine(date, datetime.time(23, 59, 59))
        if now > deadline:
            raise ValidationError(f"A late-arrival short leave must be requested by {timezone.localtime(deadline):%d %b %Y %H:%M}.")

    req = ShortLeaveRequest(employee=employee, date=date, kind=kind, start_time=start_time, end_time=end_time,
                            duration_minutes=duration, reason=reason.strip(), policy_version=policy)
    if attachment:
        req.attachment = attachment
    return wf.submit(req, actor)


@transaction.atomic
def submit_missed_punch(employee, actor, *, date, issue_type, requested_in=None, requested_out=None, reason="",
                        attachment=None, attachment_is_cctv=False, today=None):
    today = today or timezone.localdate()
    if date > today:
        raise ValidationError({"date": "You cannot report a missed punch for a future date."})
    if date < employee.date_of_joining:
        raise ValidationError({"date": "Date is before your date of joining."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Please explain what happened."})
    needs_in = issue_type in ("MISSING_IN", "INCORRECT_IN")
    needs_out = issue_type in ("MISSING_OUT", "INCORRECT_OUT")
    if needs_in and not requested_in:
        raise ValidationError({"requested_in": "Enter the correct In time."})
    if needs_out and not requested_out:
        raise ValidationError({"requested_out": "Enter the correct Out time."})
    if requested_in and requested_out and requested_out <= requested_in:
        raise ValidationError({"requested_out": "Out time must be after In time."})
    if attachment_is_cctv and not attachment:
        raise ValidationError({"attachment": "Attach the CCTV screenshot or untick the CCTV option."})
    type(employee).objects.select_for_update().get(pk=employee.pk)
    if MissedPunchRequest.objects.filter(employee=employee, date=date, status__in=OPEN_STATUSES).exists():
        raise ValidationError("You already have an open missed-punch request for this date.")
    _, _, _, orig_in, orig_out = effective_punches(employee, date)
    req = MissedPunchRequest(employee=employee, date=date, issue_type=issue_type, original_in=orig_in, original_out=orig_out,
                             requested_in=requested_in, requested_out=requested_out, reason=reason.strip(),
                             attachment_is_cctv=bool(attachment_is_cctv and attachment))
    if attachment:
        req.attachment = attachment
    return wf.submit(req, actor)


def submit_on_duty(employee, actor, *, start_at, end_at, destination, purpose, remarks="", attachment=None, now=None):
    now = now or timezone.now()
    if end_at <= start_at:
        raise ValidationError({"end_at": "End must be after start."})
    if not destination.strip() or not purpose.strip():
        raise ValidationError("Destination and purpose are required.")
    if (end_at - start_at).days > 31:
        raise ValidationError({"end_at": "An on-duty request cannot exceed 31 days; submit separate requests."})
    policy = _policy(employee, timezone.localtime(start_at).date())
    if policy.get("on_duty_requires_prior_submission") and start_at <= now:
        raise ValidationError({"start_at": "On-duty must be requested and approved before you leave the office or worksite."})
    overlap = OnDutyRequest.objects.filter(employee=employee, status__in=OPEN_STATUSES + (Status.APPROVED,),
                                           start_at__lt=end_at, end_at__gt=start_at)
    if overlap.exists():
        raise ValidationError("This overlaps another on-duty request.")
    req = OnDutyRequest(employee=employee, start_at=start_at, end_at=end_at, destination=destination.strip(),
                        purpose=purpose.strip(), remarks=remarks.strip())
    if attachment:
        req.attachment = attachment
    return wf.submit(req, actor)


def add_half_day_explanation(review, actor, explanation):
    me = getattr(actor, "employee", None)
    if not me or me.pk != review.employee_id:
        raise ValidationError("You can only explain your own attendance flags.")
    if not review.is_open:
        raise ValidationError("This review has already been decided.")
    if not (explanation or "").strip():
        raise ValidationError("Please enter an explanation.")
    review.employee_explanation = explanation.strip()
    review.origin = HalfDayReview.Origin.EMPLOYEE
    review.save(update_fields=["employee_explanation", "origin", "updated_at"])
    if review.status == Status.NEEDS_INFORMATION:
        wf.respond(review, actor, explanation)
    else:
        wf._record(review, actor, "RESPOND", review.status, explanation)
    return review


def record_for(employee, d):
    return AttendanceRecord.objects.filter(employee=employee, date=d).first()
