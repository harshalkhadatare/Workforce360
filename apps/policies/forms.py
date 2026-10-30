import datetime
from decimal import Decimal, InvalidOperation

from django import forms

from apps.core.forms import DateInput, StyledForm, StyledModelForm, TimeInput
from apps.employees.models import Department
from apps.leave_management.models import LeaveType

from .defaults import DEFAULTS
from .models import ApprovalChainConfig, Holiday, PolicyVersion, WorkSchedule


class PolicyVersionForm(StyledForm):
    """Builds one field per policy parameter from the catalogue in defaults.py."""

    name = forms.CharField(max_length=150)
    source_document = forms.CharField(max_length=200, required=False)
    effective_from = forms.DateField(widget=DateInput(), help_text="Records dated before this keep using the earlier version.")
    departments = forms.ModelMultipleChoiceField(Department.objects.filter(is_active=True), required=False,
                                                 widget=forms.SelectMultiple(attrs={"size": 4}),
                                                 help_text="Leave empty for a company-wide policy.")
    change_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), help_text="Required. Recorded in the policy history and audit log.")
    activate = forms.BooleanField(required=False, label="Activate immediately (otherwise saved as draft)")
    confirm = forms.BooleanField(required=True, label="I understand this affects attendance/leave calculations for dates on or after the effective date.")

    def __init__(self, kind, *args, base=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.catalogue = DEFAULTS[kind]
        for key, spec in self.catalogue.items():
            initial = base.get(key) if base else spec["default"]
            help_text = f"Policy: {spec['clause']}"
            t = spec["type"]
            if t == "bool":
                f = forms.BooleanField(required=False, initial=initial, label=spec["label"], help_text=help_text,
                                       widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
            elif t == "choice":
                f = forms.ChoiceField(choices=spec["choices"], initial=initial, label=spec["label"], help_text=help_text,
                                      widget=forms.Select(attrs={"class": "form-select"}))
            elif t == "int":
                f = forms.IntegerField(min_value=0, initial=initial, label=spec["label"], help_text=help_text,
                                       widget=forms.NumberInput(attrs={"class": "form-control"}))
            elif t == "time":
                f = forms.TimeField(initial=initial, label=spec["label"], help_text=help_text, widget=TimeInput(attrs={"class": "form-control"}))
            else:
                f = forms.DecimalField(min_value=0, max_digits=6, decimal_places=2, initial=initial, label=spec["label"], help_text=help_text,
                                       widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}))
            f.needs_hr = spec["needs_hr"]
            self.fields[f"p_{key}"] = f

    def parameter_fields(self):
        return [self[f"p_{k}"] for k in self.catalogue]

    def parameters(self):
        out = {}
        for key, spec in self.catalogue.items():
            v = self.cleaned_data.get(f"p_{key}")
            if spec["type"] == "time" and isinstance(v, datetime.time):
                v = v.strftime("%H:%M")
            elif spec["type"] == "decimal":
                try:
                    v = str(Decimal(v).normalize())
                except (InvalidOperation, TypeError):
                    v = str(spec["default"])
                v = int(v) if v.isdigit() else v
            out[key] = v
        return out


class LeaveTypeForm(StyledModelForm):
    class Meta:
        model = LeaveType
        fields = ["code", "name", "description", "tracks_balance", "is_paid", "requires_attachment", "allow_half_day",
                  "is_policy_entitlement", "is_active", "display_order"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class HolidayForm(StyledModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "kind", "location", "is_active"]
        widgets = {"date": DateInput()}


class ScheduleForm(StyledModelForm):
    weekly_offs = forms.MultipleChoiceField(choices=[(str(i), d) for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])],
                                            widget=forms.CheckboxSelectMultiple, required=False, label="Weekly off days")

    class Meta:
        model = WorkSchedule
        fields = ["code", "name", "start_time", "end_time", "effective_from", "is_default", "change_reason"]
        widgets = {"start_time": TimeInput(), "end_time": TimeInput(), "effective_from": DateInput()}
        help_texts = {"code": "Same code = a new version of an existing schedule.", "change_reason": "Required for changes."}

    def clean_change_reason(self):
        v = self.cleaned_data.get("change_reason", "").strip()
        if not v:
            raise forms.ValidationError("A reason is required.")
        return v


class ApprovalChainForm(StyledForm):
    request_type = forms.ChoiceField(choices=[(k, k.replace("_", " ").title()) for k in
                                              ["LEAVE", "EMERGENCY_LEAVE", "SHORT_LEAVE", "MISSED_PUNCH", "ON_DUTY", "HALF_DAY_REVIEW"]])
    step1 = forms.ChoiceField(choices=[(s, s.title()) for s in ApprovalChainConfig.STEP_CHOICES])
    step2 = forms.ChoiceField(choices=[("", "—")] + [(s, s.title()) for s in ApprovalChainConfig.STEP_CHOICES], required=False)
    step3 = forms.ChoiceField(choices=[("", "—")] + [(s, s.title()) for s in ApprovalChainConfig.STEP_CHOICES], required=False)

    def steps(self):
        return [s for s in (self.cleaned_data["step1"], self.cleaned_data.get("step2"), self.cleaned_data.get("step3")) if s]
