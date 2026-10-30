"""Leave policy engine (Leave Policy for HO — No. 001 R2).

Balance model
-------------
The balance is never stored as an editable number. It is the SUM of signed
LeaveLedgerEntry quantities. Each business event posts with a unique
idempotency key, so retries or double clicks cannot debit twice:

  ENT-<entitlement id>              entitlement credit
  LEAVE-DEBIT-<request id>-<year>   approved leave
  LEAVE-REVERSAL-<request id>-<year> cancellation of approved leave

Displayed figures:
  closing balance = SUM(ledger)
  pending         = days in requests still awaiting a decision
  available       = closing balance − pending   (pending is *reserved*, not debited)
"""
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.approvals.models import OPEN_STATUSES, Status
from apps.audit import services as audit
from apps.policies.models import PolicyVersion
from apps.policies.services import PolicyNotConfigured, daterange, get_policy, holidays_between, is_weekly_off

from .models import LeaveEntitlement, LeaveLedgerEntry, LeaveRequest, LeaveType

D0 = Decimal("0")
E = LeaveLedgerEntry.EntryType


def leave_year_of(d):
    return d.year  # policy: 1 January – 31 December


def entitlement_leave_type():
    return LeaveType.objects.filter(is_policy_entitlement=True, is_active=True).first()


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------
def balance(employee, leave_type, year, exclude_request=None):
    entries = LeaveLedgerEntry.objects.filter(employee=employee, leave_type=leave_type, leave_year=year)
    by_type = {row["entry_type"]: row["total"] for row in entries.values("entry_type").annotate(total=Sum("quantity"))}
    pending_qs = LeaveRequest.objects.filter(employee=employee, leave_type=leave_type, status__in=OPEN_STATUSES,
                                             start_date__year=year)
    if exclude_request is not None and exclude_request.pk:
        pending_qs = pending_qs.exclude(pk=exclude_request.pk)
    pending = pending_qs.aggregate(t=Sum("days_requested"))["t"] or D0
    opening = (by_type.get(E.OPENING) or D0) + (by_type.get(E.CARRY_FORWARD) or D0)
    credited = by_type.get(E.CREDIT) or D0
    used = -((by_type.get(E.DEBIT) or D0) + (by_type.get(E.REVERSAL) or D0))
    adjustments = by_type.get(E.ADJUSTMENT) or D0
    closing = opening + credited - used + adjustments
    return {
        "leave_type": leave_type, "year": year, "opening": opening, "credited": credited, "used": used,
        "adjustments": adjustments, "closing": closing, "pending": pending, "available": closing - pending,
    }


def balances_for(employee, year):
    return [balance(employee, lt, year) for lt in LeaveType.objects.filter(is_active=True, tracks_balance=True)]


# ---------------------------------------------------------------------------
# Day counting incl. weekly-off / holiday rule
# ---------------------------------------------------------------------------
def _leave_days_of_other_requests(employee, start, end, exclude_pk=None):
    """dates → request for the employee's other approved or pending leave."""
    out = {}
    qs = LeaveRequest.objects.filter(employee=employee, status__in=OPEN_STATUSES + (Status.APPROVED,),
                                     start_date__lte=end, end_date__gte=start)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for lr in qs:
        for d in daterange(max(lr.start_date, start), min(lr.end_date, end)):
            out[d] = lr
    return out


def _was_present(employee, d, policy):
    from apps.attendance.models import AttendanceRecord

    rec = AttendanceRecord.objects.filter(employee=employee, date=d).first()
    if rec is None:
        return False
    present = {AttendanceRecord.DayStatus.PRESENT, AttendanceRecord.DayStatus.HALF_DAY_FLAG, AttendanceRecord.DayStatus.ON_DUTY}
    if policy.get("half_day_leave_counts_as_presence"):
        present.add(AttendanceRecord.DayStatus.HALF_DAY_LEAVE)
    return rec.status in present


def count_leave_days(employee, start, end, duration_type, half_day_session, policy, exclude_pk=None):
    """Return (total_days, breakdown, warnings).

    Weekly off / declared holiday rule:
      A non-working day is counted as leave only when the nearest working day
      BEFORE it and the nearest working day AFTER it are both leave days.
      Exception: if the employee was present on either adjoining working day,
      it is paid and not counted.
    Non-working days that touch another leave request (a sandwich spanning two
    separate requests) are NOT auto-charged; they are reported as a warning for
    HR review (pending HR confirmation of this edge case).
    """
    if duration_type == LeaveRequest.Duration.HALF:
        if start != end:
            raise ValidationError("A half-day leave must start and end on the same date.")
        hol = holidays_between(start, end, employee.work_location)
        if is_weekly_off(employee, start) or start in hol:
            raise ValidationError("Half-day leave cannot be taken on a weekly off or holiday.")
        label = "First half" if half_day_session == "FIRST" else "Second half"
        return Decimal("0.5"), [{"date": start.isoformat(), "count": "0.5", "label": f"Working day — {label}"}], []

    margin = datetime.timedelta(days=21)
    hol = holidays_between(start - margin, end + margin, employee.work_location)
    others = _leave_days_of_other_requests(employee, start - margin, end + margin, exclude_pk)

    def working(d):
        return not (is_weekly_off(employee, d) or d in hol)

    def is_leave_day(d):
        return (start <= d <= end) or d in others

    def nearest_working(d, step):
        cur = d + datetime.timedelta(days=step)
        for _ in range(30):
            if working(cur):
                return cur
            cur += datetime.timedelta(days=step)
        return None

    total, breakdown, warnings = D0, [], []
    for d in daterange(start, end):
        if working(d):
            total += 1
            breakdown.append({"date": d.isoformat(), "count": "1", "label": "Working day"})
            continue
        kind = hol[d].name if d in hol else "Weekly off"
        if not policy.get("sandwich_rule_enabled"):
            breakdown.append({"date": d.isoformat(), "count": "0", "label": f"{kind} — not counted (rule disabled)"})
            continue
        prev_wd, next_wd = nearest_working(d, -1), nearest_working(d, 1)
        sandwiched = prev_wd is not None and next_wd is not None and is_leave_day(prev_wd) and is_leave_day(next_wd)
        if not sandwiched:
            breakdown.append({"date": d.isoformat(), "count": "0", "label": f"{kind} — not between two leave days, not counted"})
            continue
        if policy.get("sandwich_presence_exception") and (_was_present(employee, prev_wd, policy) or _was_present(employee, next_wd, policy)):
            breakdown.append({"date": d.isoformat(), "count": "0", "label": f"{kind} — present on adjoining working day, paid (policy exception)"})
            continue
        total += 1
        breakdown.append({"date": d.isoformat(), "count": "1", "label": f"{kind} — between two leave days, counted as leave"})

    # Cross-request sandwiches just outside the range: flag only.
    for edge, step in ((start, -1), (end, 1)):
        d = edge + datetime.timedelta(days=step)
        block = []
        while not working(d) and len(block) < 30:
            block.append(d)
            d += datetime.timedelta(days=step)
        if block and d in others:
            warnings.append(
                f"{len(block)} weekly off/holiday day(s) lie between this leave and another leave request "
                f"({min(block):%d %b}–{max(block):%d %b}). They have not been counted automatically and are flagged for HR review."
            )
    return total, breakdown, warnings


# ---------------------------------------------------------------------------
# Request creation & validation
# ---------------------------------------------------------------------------
def emergency_form_deadline(employee, rejoining_date, policy):
    days = int(policy.get("emergency_form_deadline_days"))
    if policy.get("emergency_deadline_day_type") == "WORKING":
        hol = holidays_between(rejoining_date, rejoining_date + datetime.timedelta(days=days + 21), employee.work_location)
        d, counted = rejoining_date, 0
        while counted < days:
            d += datetime.timedelta(days=1)
            if not (is_weekly_off(employee, d) or d in hol):
                counted += 1
        return d
    return rejoining_date + datetime.timedelta(days=days)


def build_leave_request(employee, actor, *, leave_type, start_date, end_date, duration_type="FULL", half_day_session="",
                        reason="", attachment=None, is_emergency=False, rejoining_date=None, signed_form=None, today=None):
    """Validate against the policy effective on the start date and return an unsaved LeaveRequest."""
    today = today or timezone.localdate()
    errors = {}
    if not leave_type or not leave_type.is_active:
        raise ValidationError({"leave_type": "Select an active leave type."})
    if end_date < start_date:
        raise ValidationError({"end_date": "End date cannot be before the start date."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Please give a reason."})
    if start_date < employee.date_of_joining:
        raise ValidationError({"start_date": "Leave cannot start before your date of joining."})
    try:
        policy = get_policy(PolicyVersion.Kind.LEAVE, start_date, employee)
    except PolicyNotConfigured as exc:
        raise ValidationError(str(exc))
    if duration_type == LeaveRequest.Duration.HALF:
        if not (policy.get("half_day_leave_allowed") and leave_type.allow_half_day):
            raise ValidationError({"duration_type": "Half-day leave is not permitted for this leave type."})
        if half_day_session not in ("FIRST", "SECOND"):
            raise ValidationError({"half_day_session": "Choose first or second half."})
    else:
        half_day_session = ""
    if leave_type.requires_attachment and not attachment:
        raise ValidationError({"attachment": "This leave type requires a supporting document."})

    late_application = False
    form_deadline = None
    if is_emergency:
        if not rejoining_date:
            raise ValidationError({"rejoining_date": "Enter the date you rejoined (or will rejoin) duty."})
        if rejoining_date <= end_date:
            raise ValidationError({"rejoining_date": "Rejoining date must be after the last leave day."})
        form_deadline = emergency_form_deadline(employee, rejoining_date, policy)
    else:
        if start_date < today:
            raise ValidationError({"start_date": "Regular leave cannot start in the past. Use the emergency leave form for leave already taken."})
        notice = int(policy.get("advance_notice_days"))
        if (start_date - today).days < notice:
            if policy.get("advance_notice_enforcement") == "BLOCK":
                raise ValidationError({"start_date": f"Leave must be applied at least {notice} days before the leave date "
                                                     f"(earliest start: {today + datetime.timedelta(days=notice):%d %b %Y}). "
                                                     f"For unforeseen absence, use the emergency leave form."})
            late_application = True

    overlap = LeaveRequest.objects.filter(employee=employee, status__in=OPEN_STATUSES + (Status.APPROVED,),
                                          start_date__lte=end_date, end_date__gte=start_date)
    if overlap.exists():
        raise ValidationError("These dates overlap another leave request that is pending or approved.")

    days, breakdown, warnings = count_leave_days(employee, start_date, end_date, duration_type, half_day_session, policy)
    if days <= 0:
        raise ValidationError("The selected dates contain no leave days (only weekly offs / holidays).")

    if leave_type.tracks_balance and not policy.get("allow_negative_balance"):
        for year in sorted({start_date.year, end_date.year}):
            year_days = sum(Decimal(b["count"]) for b in breakdown if int(b["date"][:4]) == year)
            bal = balance(employee, leave_type, year)
            if year_days > bal["available"]:
                raise ValidationError(f"Insufficient {leave_type.name} balance for {year}: requested {year_days.normalize()}, "
                                      f"available {bal['available'].normalize()} (pending requests are reserved).")

    req = LeaveRequest(
        employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
        duration_type=duration_type, half_day_session=half_day_session, reason=reason.strip(),
        days_requested=days, day_breakdown=breakdown, policy_version=policy, late_application=late_application,
        is_emergency=is_emergency, rejoining_date=rejoining_date if is_emergency else None, form_deadline=form_deadline,
        payroll_review_required=bool(warnings) or not leave_type.is_paid,
    )
    if attachment:
        req.attachment = attachment
    if is_emergency and signed_form:
        req.signed_form = signed_form
        req.signed_form_uploaded_at = timezone.now()
        req.deadline_breached = today > form_deadline
        if req.deadline_breached:
            req.payroll_review_required = True
    req._warnings = warnings
    return req


@transaction.atomic
def apply_for_leave(employee, actor, **data):
    from apps.approvals import services as wf

    # Serialise submissions per employee so overlap / balance checks cannot race.
    type(employee).objects.select_for_update().get(pk=employee.pk)
    req = build_leave_request(employee, actor, **data)
    return wf.submit(req, actor)


@transaction.atomic
def upload_signed_form(req, file, actor, today=None):
    today = today or timezone.localdate()
    req = LeaveRequest.objects.select_for_update().get(pk=req.pk)
    if not req.is_emergency:
        raise ValidationError("Only emergency leave requires a signed form.")
    if req.signed_form:
        raise ValidationError("A signed form has already been submitted.")
    req.signed_form = file
    req.signed_form_uploaded_at = timezone.now()
    if req.form_deadline and today > req.form_deadline:
        req.deadline_breached = True
        req.payroll_review_required = True
    req.save()
    audit.log(actor, "leave.signed_form_uploaded", req, after={"deadline_breached": req.deadline_breached})
    return req


def flag_overdue_emergency_forms(today=None):
    """Flag (never auto-reject) emergency leave whose signed form is overdue."""
    today = today or timezone.localdate()
    qs = LeaveRequest.objects.filter(is_emergency=True, signed_form="", deadline_breached=False, form_deadline__lt=today).exclude(
        status__in=[Status.CANCELLED, Status.REJECTED])
    ids = list(qs.values_list("pk", flat=True))
    for req in LeaveRequest.objects.filter(pk__in=ids):
        req.deadline_breached = True
        req.payroll_review_required = True
        req.save(update_fields=["deadline_breached", "payroll_review_required", "updated_at"])
        audit.log(None, "leave.emergency_form_overdue", req, reason=f"Signed form not received by {req.form_deadline}")
    return ids


# ---------------------------------------------------------------------------
# Ledger postings (called from the approval workflow inside its transaction)
# ---------------------------------------------------------------------------
def _per_year(req):
    out = {}
    for b in req.day_breakdown:
        y = int(b["date"][:4])
        out[y] = out.get(y, D0) + Decimal(b["count"])
    return {y: q for y, q in out.items() if q > 0}


def post_leave_debit(req, actor):
    from apps.approvals.services import WorkflowError

    if req.leave_type.tracks_balance:
        policy = req.policy_version
        for year, qty in _per_year(req).items():
            key = f"LEAVE-DEBIT-{req.pk}-{year}"
            if LeaveLedgerEntry.objects.filter(idempotency_key=key).exists():
                continue  # already posted — never debit twice
            bal = balance(req.employee, req.leave_type, year, exclude_request=req)
            if policy and not policy.get("allow_negative_balance") and qty > bal["available"]:
                raise WorkflowError(f"Approval would make the {req.leave_type.name} balance negative "
                                    f"(available {bal['available'].normalize()}, needed {qty.normalize()}).")
            LeaveLedgerEntry.objects.create(
                employee=req.employee, leave_type=req.leave_type, leave_year=year, entry_type=E.DEBIT, quantity=-qty,
                effective_date=req.start_date, reason=f"Approved leave request #{req.pk}", leave_request=req,
                idempotency_key=key, created_by=actor,
            )
    _recalc_attendance(req)


def post_leave_reversal(req, actor, reason):
    for entry in req.ledger_entries.filter(entry_type=E.DEBIT):
        key = f"LEAVE-REVERSAL-{req.pk}-{entry.leave_year}"
        if LeaveLedgerEntry.objects.filter(idempotency_key=key).exists():
            continue
        LeaveLedgerEntry.objects.create(
            employee=req.employee, leave_type=req.leave_type, leave_year=entry.leave_year, entry_type=E.REVERSAL,
            quantity=-entry.quantity, effective_date=timezone.localdate(), reason=f"Cancellation of request #{req.pk}: {reason}"[:255],
            leave_request=req, idempotency_key=key, created_by=actor,
        )
    _recalc_attendance(req)


def _recalc_attendance(req):
    from apps.attendance.services import recalculate_for_dates

    if req.start_date <= timezone.localdate():
        recalculate_for_dates(req.employee, list(daterange(req.start_date, min(req.end_date, timezone.localdate()))))


@transaction.atomic
def manual_adjustment(employee, leave_type, quantity, effective_date, reason, actor, entry_type=E.ADJUSTMENT):
    quantity = Decimal(quantity)
    if quantity == 0:
        raise ValidationError("Quantity cannot be zero.")
    if not (reason or "").strip():
        raise ValidationError("A reason is required for manual adjustments.")
    if entry_type not in (E.ADJUSTMENT, E.OPENING, E.CARRY_FORWARD):
        raise ValidationError("Invalid entry type.")
    year = leave_year_of(effective_date)
    bal = balance(employee, leave_type, year)
    policy = get_policy(PolicyVersion.Kind.LEAVE, effective_date, employee)
    if bal["closing"] + quantity < 0 and not policy.get("allow_negative_balance"):
        raise ValidationError("This adjustment would make the balance negative.")
    entry = LeaveLedgerEntry.objects.create(
        employee=employee, leave_type=leave_type, leave_year=year, entry_type=entry_type, quantity=quantity,
        effective_date=effective_date, reason=reason.strip()[:255], idempotency_key=f"ADJ-{uuid.uuid4().hex}", created_by=actor,
    )
    audit.log(actor, "leave.balance_adjusted", entry, employee_ref=employee.employee_id, reason=reason,
              after={"leave_type": leave_type.code, "quantity": quantity, "year": year, "entry_type": entry_type})
    return entry


# ---------------------------------------------------------------------------
# Entitlements
# ---------------------------------------------------------------------------
def _round(qty, step):
    step = Decimal(str(step))
    if step <= 0:
        return qty
    return (qty / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return datetime.date(d.year + y, m + 1, 1)


def compute_entitlements(employee, year, as_of=None):
    """Return a list of dicts describing the entitlements due for `year` up to `as_of`.

    Pure calculation — nothing is written. See docs/POLICY_TRACEABILITY.md (L-01..L-04)."""
    as_of = as_of or timezone.localdate()
    year_start, year_end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    out = []
    conf = employee.confirmation_date if employee.probation_status == employee.Probation.CONFIRMED else None

    # Confirmed: full year or prorated from confirmation date
    if conf and conf <= year_end:
        policy = get_policy(PolicyVersion.Kind.LEAVE, max(conf, year_start), employee)
        annual = Decimal(str(policy.get("annual_entitlement_days")))
        if conf < year_start:
            if as_of >= year_start:
                out.append({"basis": LeaveEntitlement.Basis.ANNUAL, "period_key": str(year), "quantity": annual,
                            "credit_date": year_start, "policy": policy,
                            "explanation": f"Confirmed employee (confirmed {conf:%d %b %Y}): annual entitlement of {annual.normalize()} days for {year}."})
        elif as_of >= conf:
            method = policy.get("confirmed_proration_method")
            if method == "DAYS_REMAINING":
                days_in_year = (year_end - year_start).days + 1
                remaining = (year_end - conf).days + 1
                raw = annual * Decimal(remaining) / Decimal(days_in_year)
                basis_txt = f"{remaining}/{days_in_year} days remaining"
            else:
                months = 12 - conf.month + 1
                raw = annual * Decimal(months) / Decimal(12)
                basis_txt = f"{months}/12 months remaining (incl. confirmation month)"
            qty = _round(raw, policy.get("proration_rounding"))
            out.append({"basis": LeaveEntitlement.Basis.PRORATED, "period_key": str(year), "quantity": qty, "credit_date": conf,
                        "policy": policy,
                        "explanation": f"Confirmed on {conf:%d %b %Y}: {annual.normalize()} × {basis_txt} = {raw.quantize(Decimal('0.01'))}, "
                                       f"rounded to {qty.normalize()} days. [Proration method awaiting HR confirmation unless marked confirmed]"})

    # Probation monthly credits (months of service before confirmation)
    doj = employee.effective_probation_start
    already = D0  # simulated from joining, so the cap spans leave years deterministically
    n = 1
    while n <= 120:
        month_start = _add_months(doj, n - 1)  # first day of service month n (joining month = 1)
        if month_start > year_end or month_start > as_of:
            break
        if conf and month_start >= datetime.date(conf.year, conf.month, 1):
            break
        try:
            policy = get_policy(PolicyVersion.Kind.LEAVE, max(month_start, doj), employee)
        except PolicyNotConfigured:
            # No policy covered this month (e.g. before the portal's first policy version).
            # Legacy balances are brought in by HR as an OPENING ledger entry instead.
            n += 1
            continue
        first_month = int(policy.get("probation_first_credit_month"))
        monthly = Decimal(str(policy.get("probation_monthly_credit")))
        cap = Decimal(str(policy.get("probation_max_total")))
        if n >= first_month:
            qty = min(monthly, cap - already)
            if qty > 0:
                already += qty
                if month_start.year == year:
                    out.append({"basis": LeaveEntitlement.Basis.PROBATION_MONTHLY, "period_key": f"{month_start:%Y-%m}",
                                "quantity": qty, "credit_date": max(month_start, doj), "policy": policy,
                                "explanation": f"Probation credit for service month {n} ({month_start:%b %Y}); joining month counts as "
                                               f"month 1, credits from month {first_month}, capped at {cap.normalize()} days in total. "
                                               f"[Schedule awaiting HR confirmation unless marked confirmed]"})
        n += 1
    return out


@transaction.atomic
def post_entitlements(employee, year, actor=None, as_of=None):
    lt = entitlement_leave_type()
    if lt is None:
        raise ValidationError("No leave type is marked as receiving the policy entitlement.")
    created = []
    for item in compute_entitlements(employee, year, as_of):
        ent, was_created = LeaveEntitlement.objects.get_or_create(
            employee=employee, leave_type=lt, basis=item["basis"], period_key=item["period_key"],
            defaults=dict(leave_year=year, quantity=item["quantity"], explanation=item["explanation"],
                          policy_version=item["policy"], credit_date=item["credit_date"], created_by=actor),
        )
        if not was_created:
            continue
        try:
            with transaction.atomic():
                LeaveLedgerEntry.objects.create(
                    employee=employee, leave_type=lt, leave_year=year, entry_type=E.CREDIT, quantity=ent.quantity,
                    effective_date=ent.credit_date, reason=ent.get_basis_display(), entitlement=ent,
                    idempotency_key=f"ENT-{ent.pk}", created_by=actor,
                )
        except IntegrityError:
            continue
        created.append(ent)
    if created:
        audit.log(actor, "leave.entitlements_posted", employee, after={"year": year, "count": len(created),
                                                                        "days": sum(e.quantity for e in created)})
    return created
