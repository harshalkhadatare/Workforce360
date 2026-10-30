from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, StyledForm
from apps.core.validators import validate_document

from .models import LeaveLedgerEntry, LeaveRequest, LeaveType


class LeaveApplyForm(StyledForm):
    leave_type = forms.ModelChoiceField(LeaveType.objects.filter(is_active=True))
    start_date = forms.DateField(widget=DateInput())
    end_date = forms.DateField(widget=DateInput())
    duration_type = forms.ChoiceField(label="Duration", choices=LeaveRequest.Duration.choices, initial="FULL")
    half_day_session = forms.ChoiceField(label="Which half?", choices=[("", "—")] + list(LeaveRequest.Session.choices), required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    attachment = forms.FileField(required=False, label="Supporting document", help_text="Required for some leave types. PDF, JPG or PNG.")

    def __init__(self, *args, emergency=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.emergency = emergency
        if emergency:
            self.fields["rejoining_date"] = forms.DateField(widget=DateInput(attrs={"class": "form-control"}),
                                                            help_text="The date you returned (or will return) to work.")
            self.fields["signed_form"] = forms.FileField(required=False, label="Signed leave form",
                                                         help_text="Upload now if available, otherwise within the deadline after rejoining.",
                                                         widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
            self.fields["start_date"].help_text = "Emergency leave may be for dates already taken."

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f:
            validate_document(f)
        return f

    def clean_signed_form(self):
        f = self.cleaned_data.get("signed_form")
        if f:
            validate_document(f)
        return f


class AdjustmentForm(StyledForm):
    leave_type = forms.ModelChoiceField(LeaveType.objects.filter(is_active=True, tracks_balance=True))
    entry_type = forms.ChoiceField(choices=[(LeaveLedgerEntry.EntryType.ADJUSTMENT, "Manual adjustment"),
                                            (LeaveLedgerEntry.EntryType.OPENING, "Opening balance"),
                                            (LeaveLedgerEntry.EntryType.CARRY_FORWARD, "Carry forward")])
    quantity = forms.DecimalField(max_digits=6, decimal_places=2, help_text="Use a negative number to deduct.")
    effective_date = forms.DateField(widget=DateInput(), initial=timezone.localdate)
    reason = forms.CharField(max_length=255)
