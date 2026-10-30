from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.approvals.models import ApprovalRequest
from apps.core.models import TimeStampedModel
from apps.core.storage import private_storage, upload_request_attachment
from apps.core.validators import validate_document


class LeaveType(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    tracks_balance = models.BooleanField(default=True, help_text="If off (e.g. leave without pay), no balance is checked or debited")
    is_paid = models.BooleanField(default=True)
    requires_attachment = models.BooleanField(default=False)
    allow_half_day = models.BooleanField(default=True)
    is_policy_entitlement = models.BooleanField(default=False, help_text="Receives the annual / probation entitlement from the leave policy")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["is_policy_entitlement"], condition=Q(is_policy_entitlement=True), name="single_entitlement_leave_type"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class LeaveEntitlement(TimeStampedModel):
    """Calculated entitlement for an employee / leave year with its basis.

    Posting an entitlement creates exactly one ledger CREDIT (idempotency key)."""

    class Basis(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual entitlement (confirmed)"
        PRORATED = "PRORATED", "Prorated from confirmation date"
        PROBATION_MONTHLY = "PROBATION", "Probation monthly credit"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="leave_entitlements")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    leave_year = models.PositiveSmallIntegerField()
    basis = models.CharField(max_length=10, choices=Basis.choices)
    period_key = models.CharField(max_length=20, help_text="Year, or YYYY-MM for monthly credits")
    quantity = models.DecimalField(max_digits=6, decimal_places=2)
    explanation = models.TextField()
    policy_version = models.ForeignKey("policies.PolicyVersion", on_delete=models.PROTECT, related_name="+")
    credit_date = models.DateField()
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-leave_year", "credit_date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "leave_type", "basis", "period_key"], name="one_entitlement_per_period"),
        ]


class LeaveLedgerEntry(models.Model):
    """Append-only ledger. Balance = SUM(quantity). Quantities are signed."""

    class EntryType(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit (approved leave)"
        REVERSAL = "REVERSAL", "Reversal"
        CARRY_FORWARD = "CARRY_FWD", "Carry forward"
        ADJUSTMENT = "ADJUSTMENT", "Manual adjustment"
        OPENING = "OPENING", "Opening balance"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="leave_ledger")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    leave_year = models.PositiveSmallIntegerField(db_index=True)
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    quantity = models.DecimalField(max_digits=6, decimal_places=2)
    effective_date = models.DateField()
    reason = models.CharField(max_length=255)
    leave_request = models.ForeignKey("LeaveRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    entitlement = models.ForeignKey(LeaveEntitlement, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    idempotency_key = models.CharField(max_length=120, unique=True, help_text="Prevents the same business event posting twice")
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["effective_date", "id"]
        indexes = [models.Index(fields=["employee", "leave_type", "leave_year"])]
        constraints = [models.CheckConstraint(condition=~Q(quantity=0), name="ledger_quantity_nonzero")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Ledger entries are immutable; post a reversal or adjustment instead.")
        super().save(*args, **kwargs)


class LeaveRequest(ApprovalRequest):
    REQUEST_TYPE = "LEAVE"
    TYPE_LABEL = "Leave"
    URL_SLUG = "leave"

    class Duration(models.TextChoices):
        FULL = "FULL", "Full day(s)"
        HALF = "HALF", "Half day"

    class Session(models.TextChoices):
        FIRST = "FIRST", "First half"
        SECOND = "SECOND", "Second half"

    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    duration_type = models.CharField(max_length=4, choices=Duration.choices, default=Duration.FULL)
    half_day_session = models.CharField(max_length=6, choices=Session.choices, blank=True)
    reason = models.TextField()
    days_requested = models.DecimalField(max_digits=5, decimal_places=2)
    day_breakdown = models.JSONField(default=list, help_text="Per-date counting explanation")
    attachment = models.FileField(storage=private_storage, upload_to=upload_request_attachment, blank=True, validators=[validate_document])
    policy_version = models.ForeignKey("policies.PolicyVersion", null=True, on_delete=models.PROTECT, related_name="+")
    late_application = models.BooleanField(default=False, help_text="Submitted with less than the required advance notice (FLAG mode)")

    # Emergency leave
    is_emergency = models.BooleanField(default=False)
    rejoining_date = models.DateField(null=True, blank=True)
    signed_form = models.FileField(storage=private_storage, upload_to=upload_request_attachment, blank=True, validators=[validate_document])
    signed_form_uploaded_at = models.DateTimeField(null=True, blank=True)
    form_deadline = models.DateField(null=True, blank=True)
    deadline_breached = models.BooleanField(default=False, help_text="Signed form late — flagged for HR review, never auto-changed")
    payroll_review_required = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="leave_end_after_start"),
            models.CheckConstraint(condition=Q(days_requested__gte=0), name="leave_days_nonnegative"),
        ]

    def workflow_type(self):
        return "EMERGENCY_LEAVE" if self.is_emergency else "LEAVE"

    def summary(self):
        span = f"{self.start_date:%d %b %Y}" if self.start_date == self.end_date else f"{self.start_date:%d %b} – {self.end_date:%d %b %Y}"
        kind = "Emergency leave" if self.is_emergency else self.leave_type.name
        return f"{kind}, {span} ({self.days_requested.normalize()} day{'s' if self.days_requested != 1 else ''})"

    def notification_details(self):
        rows = [("Leave type", self.leave_type.name), ("From", f"{self.start_date:%d %b %Y}"), ("To", f"{self.end_date:%d %b %Y}"),
                ("Days", str(self.days_requested.normalize())), ("Reason", self.reason)]
        if self.is_emergency:
            rows.append(("Signed form due by", f"{self.form_deadline:%d %b %Y}" if self.form_deadline else "—"))
        return rows

    @property
    def is_half_day(self):
        return self.duration_type == self.Duration.HALF

    def on_approved(self, actor):
        from .services import post_leave_debit

        post_leave_debit(self, actor)

    def on_cancel_approved(self, actor, reason):
        from .services import post_leave_reversal

        post_leave_reversal(self, actor, reason)

    @property
    def quantity(self):
        return Decimal(self.days_requested)
