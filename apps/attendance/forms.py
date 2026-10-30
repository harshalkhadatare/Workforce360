from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, DateTimeInput, StyledForm, TimeInput
from apps.core.validators import validate_document

from .models import MissedPunchRequest, ShortLeaveRequest


class _AttachmentMixin:
    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f:
            validate_document(f)
        return f


class ShortLeaveForm(_AttachmentMixin, StyledForm):
    date = forms.DateField(widget=DateInput(), initial=timezone.localdate)
    kind = forms.ChoiceField(label="Short leave for", choices=ShortLeaveRequest.Kind.choices)
    start_time = forms.TimeField(widget=TimeInput())
    end_time = forms.TimeField(widget=TimeInput(), help_text="Maximum two hours.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    attachment = forms.FileField(required=False, label="Supporting document (optional)")


class MissedPunchForm(_AttachmentMixin, StyledForm):
    date = forms.DateField(label="Attendance date", widget=DateInput())
    issue_type = forms.ChoiceField(label="What is wrong?", choices=MissedPunchRequest.Issue.choices)
    requested_in = forms.TimeField(label="Correct In time", required=False, widget=TimeInput())
    requested_out = forms.TimeField(label="Correct Out time", required=False, widget=TimeInput())
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    attachment = forms.FileField(required=False, label="Supporting evidence", help_text="PDF, JPG or PNG up to 5 MB.")
    attachment_is_cctv = forms.BooleanField(required=False, label="This is a CCTV screenshot (authorised use only — restricted to reviewers)")


class OnDutyForm(_AttachmentMixin, StyledForm):
    start_at = forms.DateTimeField(label="From", widget=DateTimeInput())
    end_at = forms.DateTimeField(label="To", widget=DateTimeInput())
    destination = forms.CharField(label="Work location / destination", max_length=200)
    purpose = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    attachment = forms.FileField(required=False, label="On-Duty form (if required)")


class AdminCorrectionForm(StyledForm):
    corrected_in = forms.TimeField(required=False, widget=TimeInput())
    corrected_out = forms.TimeField(required=False, widget=TimeInput())
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        c = super().clean()
        if not c.get("corrected_in") and not c.get("corrected_out"):
            raise forms.ValidationError("Enter a corrected In and/or Out time.")
        return c
