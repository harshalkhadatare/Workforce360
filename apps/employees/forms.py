from django import forms

from apps.accounts.models import Role
from apps.core.forms import DateInput, StyledForm, StyledModelForm
from apps.core.validators import validate_document, validate_image

from .models import Address, BankDetail, Department, Designation, Employee, EmployeeDocument, FamilyMember, IdentityDetail, Qualification


class DirectoryFilterForm(StyledForm):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "Employee ID or name"}))
    department = forms.ModelChoiceField(Department.objects.filter(is_active=True), required=False, empty_label="All departments")
    status = forms.ChoiceField(choices=[("", "All statuses")] + list(Employee.Status.choices), required=False)
    active = forms.ChoiceField(choices=[("1", "Active only"), ("0", "Deactivated"), ("", "All")], required=False, initial="1")


class EmployeeCreateForm(StyledModelForm):
    create_login = forms.BooleanField(required=False, initial=True, label="Create a portal login and email a password set-up link")
    role = forms.ChoiceField(choices=Role.choices, initial=Role.EMPLOYEE, label="Portal role")

    class Meta:
        model = Employee
        fields = ["employee_id", "first_name", "middle_name", "last_name", "official_email", "mobile", "department", "designation",
                  "reporting_manager", "date_of_joining", "probation_start_date", "employment_type", "work_location", "work_schedule"]
        widgets = {"date_of_joining": DateInput(), "probation_start_date": DateInput()}
        help_texts = {"employee_id": "Becomes the login username. Cannot be changed later.",
                      "probation_start_date": "Leave blank if it is the same as the joining date."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reporting_manager"].queryset = Employee.objects.filter(is_active=True).order_by("first_name")
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["designation"].queryset = Designation.objects.filter(is_active=True)
        from apps.policies.models import WorkSchedule

        self.fields["work_schedule"].queryset = WorkSchedule.objects.all()
        self.fields["work_schedule"].help_text = "Leave blank to use the default schedule."

    def clean_employee_id(self):
        from apps.accounts.models import normalize_employee_id

        v = normalize_employee_id(self.cleaned_data["employee_id"])
        if Employee.objects.filter(employee_id__iexact=v).exists():
            raise forms.ValidationError("This Employee ID already exists.")
        return v


class PersonalHRForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["first_name", "middle_name", "last_name", "date_of_birth", "gender", "marital_status", "blood_group",
                  "emergency_contact_name", "emergency_contact_relation", "emergency_contact_phone", "photo"]
        widgets = {"date_of_birth": DateInput()}

    def clean_photo(self):
        p = self.cleaned_data.get("photo")
        if p and hasattr(p, "size") and getattr(p, "content_type", None):
            validate_image(p)
        return p


class PersonalSelfForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["marital_status", "blood_group", "emergency_contact_name", "emergency_contact_relation", "emergency_contact_phone"]


class ContactHRForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["official_email", "personal_email", "mobile", "alternate_mobile"]


class ContactSelfForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["personal_email", "mobile", "alternate_mobile"]


class AddressForm(StyledModelForm):
    class Meta:
        model = Address
        fields = ["line1", "line2", "city", "state", "pin_code", "country"]


class IdentityForm(StyledModelForm):
    class Meta:
        model = IdentityDetail
        fields = ["pan_number", "aadhaar_number", "passport_number", "passport_valid_until", "other_id_type", "other_id_number"]
        widgets = {"passport_valid_until": DateInput()}
        help_texts = {"aadhaar_number": "Record only if the company requires it. Displayed masked."}

    def clean_pan_number(self):
        return (self.cleaned_data.get("pan_number") or "").upper().strip()


class QualificationForm(StyledModelForm):
    class Meta:
        model = Qualification
        fields = ["qualification", "specialization", "institution", "university_or_board", "passing_year", "score"]


class EmploymentForm(StyledModelForm):
    effective_date = forms.DateField(widget=DateInput(), help_text="When these changes take effect (recorded in employment history).")
    reason = forms.CharField(max_length=255, help_text="Why the change is being made (recorded in history and audit log).")

    class Meta:
        model = Employee
        fields = ["department", "designation", "reporting_manager", "date_of_joining", "probation_start_date", "probation_status",
                  "employment_type", "employment_status", "work_location", "work_schedule", "exit_date"]
        widgets = {"date_of_joining": DateInput(), "probation_start_date": DateInput(), "exit_date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reporting_manager"].queryset = Employee.objects.filter(is_active=True).exclude(pk=self.instance.pk)
        self.fields["probation_status"].choices = [c for c in Employee.Probation.choices if c[0] != Employee.Probation.CONFIRMED] \
            if self.instance.probation_status != Employee.Probation.CONFIRMED else Employee.Probation.choices
        self.fields["probation_status"].help_text = "Use the 'Confirm employee' action to confirm."


class ConfirmEmployeeForm(StyledForm):
    confirmation_date = forms.DateField(widget=DateInput())
    reason = forms.CharField(max_length=255, label="Approval reference / reason")


class BankForm(StyledModelForm):
    class Meta:
        model = BankDetail
        fields = ["bank_name", "account_holder_name", "account_number", "ifsc_code", "branch", "verification_status"]

    def clean_ifsc_code(self):
        return (self.cleaned_data.get("ifsc_code") or "").upper().strip()


class FamilyForm(StyledModelForm):
    class Meta:
        model = FamilyMember
        fields = ["name", "relationship", "date_of_birth", "contact_number", "is_dependent", "is_nominee"]
        widgets = {"date_of_birth": DateInput()}


class DocumentForm(StyledModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ["category", "title", "file", "visible_to_employee"]
        help_texts = {"file": "PDF, JPG or PNG, up to 5 MB."}

    def __init__(self, *args, hr=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not hr:
            self.fields["category"].choices = [c for c in EmployeeDocument.Category.choices if c[0] in ("EDUCATION", "LEAVE_FORM", "OTHER")]
            del self.fields["visible_to_employee"]

    def clean_file(self):
        f = self.cleaned_data["file"]
        validate_document(f)
        return f


class ReasonForm(StyledForm):
    reason = forms.CharField(max_length=255)
    effective_date = forms.DateField(widget=DateInput(), required=False, help_text="Exit date (for deactivation), optional")


class AccountForm(StyledForm):
    role = forms.ChoiceField(choices=Role.choices)
    is_hr = forms.BooleanField(required=False, label="HR permission group")
    is_finance = forms.BooleanField(required=False, label="Finance permission group (payroll section)")
    reason = forms.CharField(max_length=255, required=False)


class DepartmentForm(StyledModelForm):
    class Meta:
        model = Department
        fields = ["code", "name", "is_active"]


class DesignationForm(StyledModelForm):
    class Meta:
        model = Designation
        fields = ["name", "is_active"]
