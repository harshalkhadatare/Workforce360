from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class PolicyVersion(TimeStampedModel):
    """A versioned, effective-dated set of policy parameters.

    Records processed under a policy keep a FK to the version that applied,
    so historical results remain reproducible after settings change.
    """

    class Kind(models.TextChoices):
        LEAVE = "LEAVE", "Leave policy"
        ATTENDANCE = "ATTENDANCE", "Attendance policy"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        RETIRED = "RETIRED", "Retired"

    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    name = models.CharField(max_length=150)
    source_document = models.CharField(max_length=200, blank=True, help_text="e.g. Attendance Policy No. 002 R2 dated 22-06-2026")
    effective_from = models.DateField(db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    parameters = models.JSONField(default=dict)
    hr_confirmations = models.JSONField(default=dict, blank=True, help_text="Map of parameter key → {confirmed_by, confirmed_at, note}")
    departments = models.ManyToManyField("employees.Department", blank=True, help_text="Empty = applies to all departments")
    change_reason = models.TextField()
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    activated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["kind", "-effective_from", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} v{self.pk} (from {self.effective_from:%d %b %Y})"

    def get(self, key):
        from .defaults import DEFAULTS

        if key in self.parameters:
            return self.parameters[key]
        return DEFAULTS[self.kind][key]["default"]

    def is_confirmed(self, key):
        return key in (self.hr_confirmations or {})


class WorkSchedule(TimeStampedModel):
    """Shift definition. Versions share a `code`; the one effective on a date applies."""

    code = models.CharField(max_length=20, db_index=True)
    name = models.CharField(max_length=80)
    start_time = models.TimeField()
    end_time = models.TimeField()
    weekly_off_days = models.JSONField(default=list, help_text="Weekday numbers, Monday=0 … Sunday=6")
    effective_from = models.DateField()
    is_default = models.BooleanField(default=False)
    change_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["code", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["code", "effective_from"], name="schedule_code_effective_unique"),
            models.CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="schedule_end_after_start"),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    @property
    def weekly_off_labels(self):
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return ", ".join(names[d] for d in sorted(self.weekly_off_days or []))


class Holiday(TimeStampedModel):
    class Kind(models.TextChoices):
        PUBLIC = "PUBLIC", "Public holiday"
        COMPANY = "COMPANY", "Company holiday"

    date = models.DateField(db_index=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PUBLIC)
    location = models.CharField(max_length=120, blank=True, help_text="Blank = all locations")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["date", "location"], name="one_holiday_per_date_location")]

    def __str__(self):
        return f"{self.date:%d %b %Y} — {self.name}"


class ApprovalChainConfig(TimeStampedModel):
    """Configurable approval chain per request type, e.g. ["MANAGER", "HR"]."""

    STEP_CHOICES = ["MANAGER", "HR", "ADMIN"]

    request_type = models.CharField(max_length=30, unique=True)
    steps = models.JSONField(default=list)
    updated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return f"{self.request_type}: {' → '.join(self.steps)}"
