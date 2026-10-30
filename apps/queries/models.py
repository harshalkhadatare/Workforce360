from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.storage import private_storage, upload_query_attachment
from apps.core.validators import validate_document


class EmployeeQuery(TimeStampedModel):
    class Category(models.TextChoices):
        ATTENDANCE = "ATTENDANCE", "Attendance"
        LEAVE = "LEAVE", "Leave"
        PAYROLL = "PAYROLL", "Payroll"
        DETAILS = "DETAILS", "Employee details"
        ACCESS = "ACCESS", "Login and access"
        DOCUMENTS = "DOCUMENTS", "Documents"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        AWAITING_EMPLOYEE = "AWAITING_EMP", "Awaiting employee"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="queries")
    category = models.CharField(max_length=12, choices=Category.choices)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    attachment = models.FileField(storage=private_storage, upload_to=upload_query_attachment, blank=True, validators=[validate_document])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_queries")
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Employee queries"

    @property
    def reference(self):
        return f"Q-{self.pk:05d}"

    def __str__(self):
        return f"{self.reference} {self.subject}"


class QueryComment(TimeStampedModel):
    query = models.ForeignKey(EmployeeQuery, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text="Internal note — never shown to the employee")
    attachment = models.FileField(storage=private_storage, upload_to=upload_query_attachment, blank=True, validators=[validate_document])

    class Meta:
        ordering = ["created_at"]
