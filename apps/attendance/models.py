from django.db import models
from django.db.models import Q

from apps.approvals.models import ApprovalRequest, Status
from apps.core.models import TimeStampedModel
from apps.core.storage import private_storage, upload_request_attachment
from apps.core.validators import validate_document


class AttendancePunch(TimeStampedModel):
    """A single raw punch event. Imported punches are never edited in place;
    corrections are layered on top via AttendanceAdjustment, and a rolled-back
    import voids (does not delete) its punches."""

    class PunchType(models.TextChoices):
        IN = "IN", "In"
        OUT = "OUT", "Out"
        UNKNOWN = "UNKNOWN", "Unspecified"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="punches")
    punch_time = models.DateTimeField(db_index=True)
    punch_date = models.DateField(db_index=True, help_text="Local (company timezone) date of the punch")
    punch_type = models.CharField(max_length=8, choices=PunchType.choices)
    device = models.CharField(max_length=80, blank=True)
    batch = models.ForeignKey("imports.AttendanceImportBatch", null=True, blank=True, on_delete=models.PROTECT, related_name="punches")
    import_row = models.ForeignKey("imports.AttendanceImportRow", null=True, blank=True, on_delete=models.PROTECT, related_name="punches")
    source_key = models.CharField(max_length=64, db_index=True, help_text="SHA-256 business key used for duplicate detection")
    raw_value = models.CharField(max_length=120, blank=True)
    is_void = models.BooleanField(default=False, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["punch_time"]
        indexes = [models.Index(fields=["employee", "punch_date"])]
        constraints = [
            models.UniqueConstraint(fields=["source_key"], condition=Q(is_void=False), name="unique_live_punch_key"),
        ]


class AttendanceRecord(TimeStampedModel):
    """Normalised daily attendance, recomputed from punches + adjustments + approved requests."""

    class DayStatus(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        HALF_DAY_FLAG = "HALF_DAY_FLAG", "Half day (flagged)"
        ABSENT = "ABSENT", "Absent"
        INCOMPLETE = "INCOMPLETE", "Missing punch"
        LEAVE = "LEAVE", "On leave"
        HALF_DAY_LEAVE = "HALF_DAY_LEAVE", "Half-day leave"
        ON_DUTY = "ON_DUTY", "On duty"
        WEEKLY_OFF = "WEEKLY_OFF", "Weekly off"
        HOLIDAY = "HOLIDAY", "Holiday"
        NOT_IMPORTED = "NOT_IMPORTED", "No data imported"

    class FlagCode(models.TextChoices):
        NONE = "", "—"
        AFTER_CUTOFF = "AFTER_CUTOFF", "Arrived after half-day cut-off"
        EXCEEDS_DAILY = "EXCEEDS_DAILY", "Late beyond daily grace"
        EXCEEDS_MONTHLY = "EXCEEDS_MONTHLY", "Monthly grace exhausted"
        MONTHLY_RETRO = "MONTHLY_RETRO", "Monthly grace exceeded (retrospective rule)"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="attendance_records")
    date = models.DateField(db_index=True)
    schedule = models.ForeignKey("policies.WorkSchedule", null=True, on_delete=models.PROTECT, related_name="+")
    scheduled_start = models.TimeField(null=True)
    scheduled_end = models.TimeField(null=True)
    first_in = models.DateTimeField(null=True, blank=True)
    last_out = models.DateTimeField(null=True, blank=True)
    punch_count = models.PositiveSmallIntegerField(default=0)
    worked_minutes = models.PositiveIntegerField(null=True, blank=True)
    late_minutes = models.PositiveIntegerField(default=0)
    early_minutes = models.PositiveIntegerField(default=0)
    grace_minutes_used = models.PositiveIntegerField(default=0)
    month_grace_cumulative = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=15, choices=DayStatus.choices, db_index=True)
    half_day_flag = models.BooleanField(default=False, db_index=True)
    flag_code = models.CharField(max_length=20, choices=FlagCode.choices, blank=True)
    explanation = models.TextField(blank=True, help_text="Human-readable calculation explanation")
    early_departure_flag = models.BooleanField(default=False)
    is_adjusted = models.BooleanField(default=False)
    policy_version = models.ForeignKey("policies.PolicyVersion", null=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_record_per_day")]
        indexes = [models.Index(fields=["date", "status"])]

    def __str__(self):
        return f"{self.employee.employee_id} {self.date} {self.status}"

    @property
    def open_review(self):
        return self.half_day_reviews.exclude(status=Status.CANCELLED).order_by("-id").first()


class AttendanceAdjustment(TimeStampedModel):
    """Audited correction layered over imported punches. Original values are kept."""

    class Source(models.TextChoices):
        MISSED_PUNCH = "MISSED_PUNCH", "Approved missed-punch request"
        IMPORT_RECONCILIATION = "IMPORT_RECON", "Import reconciliation"
        ADMIN_CORRECTION = "ADMIN", "Administrator correction"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="attendance_adjustments")
    date = models.DateField(db_index=True)
    original_in = models.DateTimeField(null=True, blank=True)
    original_out = models.DateTimeField(null=True, blank=True)
    corrected_in = models.DateTimeField(null=True, blank=True)
    corrected_out = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    source = models.CharField(max_length=15, choices=Source.choices)
    missed_punch_request = models.OneToOneField("MissedPunchRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment")
    import_row = models.OneToOneField("imports.AttendanceImportRow", null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment")
    approved_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(corrected_in__isnull=False) | Q(corrected_out__isnull=False), name="adjustment_has_correction"),
        ]


class ShortLeaveRequest(ApprovalRequest):
    REQUEST_TYPE = "SHORT_LEAVE"
    TYPE_LABEL = "Short leave"
    URL_SLUG = "short-leave"

    class Kind(models.TextChoices):
        LATE_ARRIVAL = "LATE_ARRIVAL", "Late arrival"
        EARLY_DEPARTURE = "EARLY_DEPARTURE", "Early departure"

    date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    duration_minutes = models.PositiveSmallIntegerField()
    reason = models.TextField()
    attachment = models.FileField(storage=private_storage, upload_to=upload_request_attachment, blank=True, validators=[validate_document])
    policy_version = models.ForeignKey("policies.PolicyVersion", null=True, on_delete=models.PROTECT, related_name="+")
    team_notified = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="short_leave_end_after_start")]

    def summary(self):
        return f"{self.get_kind_display()} on {self.date:%d %b %Y}, {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def notification_details(self):
        return [("Type", self.get_kind_display()), ("Date", f"{self.date:%d %b %Y}"),
                ("Time", f"{self.start_time:%H:%M} – {self.end_time:%H:%M} ({self.duration_minutes} min)"), ("Reason", self.reason)]

    def on_approved(self, actor):
        from .services import recalculate_for_dates

        recalculate_for_dates(self.employee, [self.date])

    def on_cancel_approved(self, actor, reason):
        self.on_approved(actor)


class MissedPunchRequest(ApprovalRequest):
    REQUEST_TYPE = "MISSED_PUNCH"
    TYPE_LABEL = "Missed punch"
    URL_SLUG = "missed-punch"

    class Issue(models.TextChoices):
        MISSING_IN = "MISSING_IN", "Missing In punch"
        MISSING_OUT = "MISSING_OUT", "Missing Out punch"
        INCORRECT_IN = "INCORRECT_IN", "Incorrect In punch"
        INCORRECT_OUT = "INCORRECT_OUT", "Incorrect Out punch"
        WRONG_DATE = "WRONG_DATE", "Wrong attendance date"
        WRONG_STATUS = "WRONG_STATUS", "Incorrect attendance status"
        OTHER = "OTHER", "Other discrepancy"

    date = models.DateField(db_index=True)
    issue_type = models.CharField(max_length=15, choices=Issue.choices)
    original_in = models.DateTimeField(null=True, blank=True, help_text="Snapshot from the system at submission")
    original_out = models.DateTimeField(null=True, blank=True)
    requested_in = models.TimeField(null=True, blank=True)
    requested_out = models.TimeField(null=True, blank=True)
    reason = models.TextField()
    attachment = models.FileField(storage=private_storage, upload_to=upload_request_attachment, blank=True, validators=[validate_document])
    attachment_is_cctv = models.BooleanField(default=False, help_text="CCTV still — restricted to authorised reviewers")

    class Meta:
        ordering = ["-date", "-id"]

    def summary(self):
        return f"{self.get_issue_type_display()} on {self.date:%d %b %Y}"

    def notification_details(self):
        fmt = lambda t: t.strftime("%H:%M") if t else "—"  # noqa: E731
        return [("Date", f"{self.date:%d %b %Y}"), ("Issue", self.get_issue_type_display()),
                ("Requested In", fmt(self.requested_in)), ("Requested Out", fmt(self.requested_out)), ("Reason", self.reason)]

    def on_approved(self, actor):
        from .services import apply_missed_punch

        apply_missed_punch(self, actor)

    def on_cancel_approved(self, actor, reason):
        from .services import recalculate_for_dates

        AttendanceAdjustment.objects.filter(missed_punch_request=self).update(is_active=False)
        recalculate_for_dates(self.employee, [self.date])


class OnDutyRequest(ApprovalRequest):
    REQUEST_TYPE = "ON_DUTY"
    TYPE_LABEL = "On duty"
    URL_SLUG = "on-duty"

    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    destination = models.CharField(max_length=200)
    purpose = models.TextField()
    remarks = models.TextField(blank=True)
    attachment = models.FileField("On-Duty form", storage=private_storage, upload_to=upload_request_attachment, blank=True, validators=[validate_document])
    approved_after_start = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(end_at__gt=models.F("start_at")), name="on_duty_end_after_start")]

    def summary(self):
        from django.utils import timezone as tz

        return f"On duty at {self.destination}, {tz.localtime(self.start_at):%d %b %H:%M} – {tz.localtime(self.end_at):%d %b %H:%M}"

    def notification_details(self):
        from django.utils import timezone as tz

        return [("Destination", self.destination), ("From", f"{tz.localtime(self.start_at):%d %b %Y %H:%M}"),
                ("To", f"{tz.localtime(self.end_at):%d %b %Y %H:%M}"), ("Purpose", self.purpose)]

    def dates(self):
        from django.utils import timezone as tz

        from apps.policies.services import daterange

        return list(daterange(tz.localtime(self.start_at).date(), tz.localtime(self.end_at).date()))

    def on_approved(self, actor):
        from django.utils import timezone as tz

        from .services import recalculate_for_dates

        if tz.now() > self.start_at:
            self.approved_after_start = True
            self.save(update_fields=["approved_after_start"])
        recalculate_for_dates(self.employee, self.dates())

    def on_cancel_approved(self, actor, reason):
        from .services import recalculate_for_dates

        recalculate_for_dates(self.employee, self.dates())


class HalfDayReview(ApprovalRequest):
    """Review of an automatic half-day flag. Approve = waive the flag; Reject = confirm the half day."""

    REQUEST_TYPE = "HALF_DAY_REVIEW"
    TYPE_LABEL = "Half-day review"
    URL_SLUG = "half-day"

    class Origin(models.TextChoices):
        AUTO_FLAG = "AUTO_FLAG", "Flagged by attendance rules"
        EMPLOYEE = "EMPLOYEE", "Raised by employee"

    class Outcome(models.TextChoices):
        PENDING = "", "Pending"
        WAIVED = "WAIVED", "Flag waived"
        CONFIRMED = "CONFIRMED", "Half day confirmed"

    attendance_record = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name="half_day_reviews")
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.AUTO_FLAG)
    flag_explanation = models.TextField()
    employee_explanation = models.TextField(blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, blank=True)

    class Meta:
        ordering = ["-attendance_record__date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attendance_record"],
                condition=~Q(status=Status.CANCELLED),
                name="one_live_half_day_review_per_record",
            )
        ]

    @property
    def date(self):
        return self.attendance_record.date

    def summary(self):
        return f"Half-day flag on {self.attendance_record.date:%d %b %Y}"

    def notification_details(self):
        return [("Date", f"{self.attendance_record.date:%d %b %Y}"), ("Flag", self.flag_explanation),
                ("Employee explanation", self.employee_explanation or "—")]

    def on_approved(self, actor):
        self.outcome = self.Outcome.WAIVED
        self.save(update_fields=["outcome"])

    def on_rejected(self, actor):
        self.outcome = self.Outcome.CONFIRMED
        self.save(update_fields=["outcome"])
