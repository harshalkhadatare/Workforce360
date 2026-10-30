from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.storage import private_storage, upload_import_file

SYSTEM_FIELDS = [
    ("employee_id", "Employee ID", True),
    ("employee_name", "Employee name", False),
    ("attendance_date", "Attendance date", False),
    ("in_time", "In time", False),
    ("out_time", "Out time", False),
    ("punch_timestamp", "Punch timestamp", False),
    ("punch_type", "Punch type (IN/OUT)", False),
    ("device", "Device / location", False),
    ("status", "Status (informational)", False),
]


class ImportFormat(models.TextChoices):
    DAILY = "DAILY", "Daily rows — one row per employee per day with In and Out columns"
    PUNCH_LOG = "PUNCH_LOG", "Punch log — one row per punch with a timestamp"


class ImportTemplate(TimeStampedModel):
    """Saved column mapping for a recurring vendor format."""

    name = models.CharField(max_length=100, unique=True)
    format_mode = models.CharField(max_length=10, choices=ImportFormat.choices)
    mapping = models.JSONField(default=dict, help_text="system field → source column header")
    header_row = models.PositiveSmallIntegerField(default=1)
    dayfirst = models.BooleanField(default=True, help_text="Interpret ambiguous dates as DD/MM/YYYY")
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return self.name


class AttendanceImportBatch(TimeStampedModel):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        VALIDATED = "VALIDATED", "Validated — awaiting confirmation"
        COMPLETED = "COMPLETED", "Completed"
        COMPLETED_WITH_ERRORS = "PARTIAL", "Completed with rejected rows"
        FAILED = "FAILED", "Failed — nothing imported"
        ROLLED_BACK = "ROLLED_BACK", "Rolled back"
        DISCARDED = "DISCARDED", "Discarded"

    file = models.FileField(storage=private_storage, upload_to=upload_import_file)
    original_name = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveIntegerField()
    sheet_names = models.JSONField(default=list)
    uploaded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED, db_index=True)

    sheet_name = models.CharField(max_length=100, blank=True)
    header_row = models.PositiveSmallIntegerField(default=1)
    format_mode = models.CharField(max_length=10, choices=ImportFormat.choices, blank=True)
    mapping = models.JSONField(default=dict)
    dayfirst = models.BooleanField(default=True)
    template = models.ForeignKey(ImportTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    detected_columns = models.JSONField(default=list)

    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    warning_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    conflict_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    punches_created = models.PositiveIntegerField(default=0)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    same_file_batch = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                        help_text="Earlier batch with an identical file (re-import warning)")
    failure_message = models.TextField(blank=True)
    confirmed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rolled_back_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    rollback_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch #{self.pk} — {self.original_name}"

    @property
    def batch_code(self):
        return f"IMP-{self.pk:05d}"

    @property
    def is_final(self):
        return self.status in {self.Status.COMPLETED, self.Status.COMPLETED_WITH_ERRORS, self.Status.ROLLED_BACK,
                               self.Status.FAILED, self.Status.DISCARDED}


class AttendanceImportRow(models.Model):
    class RowStatus(models.TextChoices):
        VALID = "VALID", "Valid"
        WARNING = "WARNING", "Valid with warnings"
        ERROR = "ERROR", "Rejected"
        DUPLICATE = "DUPLICATE", "Duplicate (skipped)"
        CONFLICT = "CONFLICT", "Conflicts with earlier import — needs reconciliation"
        IMPORTED = "IMPORTED", "Imported"
        RECON_ACCEPTED = "RECON_OK", "Correction accepted"
        RECON_REJECTED = "RECON_NO", "Correction rejected (existing kept)"

    batch = models.ForeignKey(AttendanceImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField(help_text="Row number in the worksheet")
    raw_data = models.JSONField(default=dict)
    employee = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    employee_code = models.CharField(max_length=60, blank=True)
    attendance_date = models.DateField(null=True, blank=True)
    in_time = models.DateTimeField(null=True, blank=True)
    out_time = models.DateTimeField(null=True, blank=True)
    punch_time = models.DateTimeField(null=True, blank=True)
    punch_type = models.CharField(max_length=8, blank=True)
    device = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=RowStatus.choices, db_index=True)
    messages = models.JSONField(default=list)
    business_keys = models.JSONField(default=list)
    reconciled_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["row_number"]
        constraints = [models.UniqueConstraint(fields=["batch", "row_number"], name="unique_row_per_batch")]

    @property
    def message_text(self):
        return "; ".join(m.get("text", "") for m in self.messages)

    @property
    def errors(self):
        return [m["text"] for m in self.messages if m.get("level") == "error"]

    @property
    def warnings(self):
        return [m["text"] for m in self.messages if m.get("level") == "warning"]
