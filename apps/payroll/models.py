"""Restricted payroll information.

Per the brief, NO salary amounts, statutory deductions or tax are calculated
here. These records hold only Finance-approved reference fields until the
salary structure and payroll rules are approved by Finance.
"""
from django.db import models

from apps.core.models import TimeStampedModel


class PayrollProfile(TimeStampedModel):
    class PayrollStatus(models.TextChoices):
        NOT_SET_UP = "NOT_SET_UP", "Not set up"
        ACTIVE = "ACTIVE", "Active"
        ON_HOLD = "ON_HOLD", "On hold"
        STOPPED = "STOPPED", "Stopped"

    class ProcessingStatus(models.TextChoices):
        NONE = "NONE", "Not processed"
        PENDING = "PENDING", "Pending review"
        PROCESSED = "PROCESSED", "Processed externally"

    employee = models.OneToOneField("employees.Employee", on_delete=models.CASCADE, related_name="payroll")
    salary_structure = models.CharField(max_length=120, blank=True, help_text="Name/reference of the Finance-approved structure")
    effective_date = models.DateField(null=True, blank=True)
    payroll_status = models.CharField(max_length=12, choices=PayrollStatus.choices, default=PayrollStatus.NOT_SET_UP)
    approved_exemptions = models.TextField(blank=True)
    authorized_deductions = models.TextField(blank=True)
    payroll_period = models.CharField(max_length=40, blank=True, help_text="e.g. Monthly, 1st–last day")
    processing_status = models.CharField(max_length=12, choices=ProcessingStatus.choices, default=ProcessingStatus.NONE)
    remarks = models.TextField(blank=True)
    updated_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
