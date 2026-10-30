from django import forms

from apps.core.forms import DateInput, StyledModelForm

from .models import PayrollProfile


class PayrollProfileForm(StyledModelForm):
    reason = forms.CharField(max_length=255, help_text="Recorded in the audit log.")

    class Meta:
        model = PayrollProfile
        fields = ["salary_structure", "effective_date", "payroll_status", "approved_exemptions", "authorized_deductions",
                  "payroll_period", "processing_status", "remarks"]
        widgets = {"effective_date": DateInput(), "approved_exemptions": forms.Textarea(attrs={"rows": 3}),
                   "authorized_deductions": forms.Textarea(attrs={"rows": 3}), "remarks": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"salary_structure": "Reference to the Finance-approved structure. No salary amounts are calculated in this portal."}
