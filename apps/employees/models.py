from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from apps.accounts.models import employee_id_validator
from apps.core.models import TimeStampedModel
from apps.core.storage import private_storage, upload_employee_document, upload_profile_photo
from apps.core.utils import mask_value

pin_validator = RegexValidator(r"^\d{6}$", "PIN code must be 6 digits.")
phone_validator = RegexValidator(r"^\+?[0-9 \-]{7,18}$", "Enter a valid phone number.")
pan_validator = RegexValidator(r"^[A-Z]{5}[0-9]{4}[A-Z]$", "PAN must look like ABCDE1234F.")
aadhaar_validator = RegexValidator(r"^\d{12}$", "Aadhaar must be 12 digits.")
ifsc_validator = RegexValidator(r"^[A-Z]{4}0[A-Z0-9]{6}$", "IFSC must look like ABCD0123456.")
account_validator = RegexValidator(r"^\d{6,20}$", "Account number must be 6–20 digits.")


class Department(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Designation(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_NOTICE = "ON_NOTICE", "On notice"
        EXITED = "EXITED", "Exited"
        INACTIVE = "INACTIVE", "Inactive"

    class EmploymentType(models.TextChoices):
        PERMANENT = "PERMANENT", "Permanent"
        CONTRACT = "CONTRACT", "Contract"
        TRAINEE = "TRAINEE", "Trainee"
        CONSULTANT = "CONSULTANT", "Consultant"

    class Probation(models.TextChoices):
        ON_PROBATION = "ON_PROBATION", "On probation"
        CONFIRMED = "CONFIRMED", "Confirmed"
        EXTENDED = "EXTENDED", "Probation extended"
        NOT_APPLICABLE = "NA", "Not applicable"

    class Gender(models.TextChoices):
        FEMALE = "F", "Female"
        MALE = "M", "Male"
        OTHER = "O", "Other"
        UNDISCLOSED = "U", "Prefer not to say"

    employee_id = models.CharField(max_length=30, unique=True, validators=[employee_id_validator])
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    marital_status = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(storage=private_storage, upload_to=upload_profile_photo, null=True, blank=True)

    # Contact
    official_email = models.EmailField(blank=True)
    personal_email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    alternate_mobile = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_relation = models.CharField(max_length=50, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])

    # Employment
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="employees")
    designation = models.ForeignKey(Designation, null=True, blank=True, on_delete=models.PROTECT, related_name="employees")
    reporting_manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="direct_reports")
    date_of_joining = models.DateField()
    probation_start_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True, help_text="Set only by an authorised confirmation action")
    probation_status = models.CharField(max_length=15, choices=Probation.choices, default=Probation.ON_PROBATION)
    employment_type = models.CharField(max_length=15, choices=EmploymentType.choices, default=EmploymentType.PERMANENT)
    employment_status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    work_location = models.CharField(max_length=120, blank=True)
    work_schedule = models.ForeignKey("policies.WorkSchedule", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    exit_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="Soft deactivation flag; records are never deleted")

    # Other
    blood_group = models.CharField(max_length=5, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["employee_id"]
        indexes = [models.Index(fields=["department", "employment_status"]), models.Index(fields=["last_name", "first_name"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(reporting_manager=models.F("id")) | Q(reporting_manager__isnull=True),
                name="employee_not_own_manager",
            ),
            models.CheckConstraint(
                condition=Q(confirmation_date__isnull=True) | Q(confirmation_date__gte=models.F("date_of_joining")),
                name="confirmation_after_joining",
            ),
        ]

    def __str__(self):
        return f"{self.employee_id} — {self.full_name}"

    @property
    def full_name(self):
        return " ".join(p for p in [self.first_name, self.middle_name, self.last_name] if p)

    @property
    def initials(self):
        return ((self.first_name[:1] or "") + (self.last_name[:1] or "")).upper()

    @property
    def is_confirmed(self):
        return self.confirmation_date is not None and self.probation_status == self.Probation.CONFIRMED

    @property
    def effective_probation_start(self):
        return self.probation_start_date or self.date_of_joining


class ReportingRelationship(TimeStampedModel):
    """History of reporting-manager assignments. The current one has end_date NULL."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="reporting_history")
    manager = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="+")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    changed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["employee"], condition=Q(end_date__isnull=True), name="one_current_manager"),
        ]


class EmploymentRecord(TimeStampedModel):
    """Change history for important employment fields."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="employment_history")
    field = models.CharField(max_length=50)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    effective_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-effective_date", "-created_at"]


class Address(TimeStampedModel):
    class Kind(models.TextChoices):
        CURRENT = "CURRENT", "Current"
        PERMANENT = "PERMANENT", "Permanent"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="addresses")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    line1 = models.CharField("Address line 1", max_length=200)
    line2 = models.CharField("Address line 2", max_length=200, blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    pin_code = models.CharField("PIN code", max_length=6, validators=[pin_validator])
    country = models.CharField(max_length=60, default="India")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "kind"], name="one_address_per_kind")]

    def __str__(self):
        return f"{self.line1}, {self.city} {self.pin_code}"


class IdentityDetail(TimeStampedModel):
    """Restricted: PAN / Aadhaar / passport. Masked by default everywhere."""

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="identity")
    pan_number = models.CharField("PAN", max_length=10, blank=True, validators=[pan_validator])
    aadhaar_number = models.CharField("Aadhaar", max_length=12, blank=True, validators=[aadhaar_validator])
    passport_number = models.CharField(max_length=12, blank=True)
    passport_valid_until = models.DateField(null=True, blank=True)
    other_id_type = models.CharField(max_length=60, blank=True)
    other_id_number = models.CharField(max_length=40, blank=True)

    @property
    def pan_masked(self):
        return mask_value(self.pan_number, 4)

    @property
    def aadhaar_masked(self):
        return mask_value(self.aadhaar_number, 4)

    @property
    def passport_masked(self):
        return mask_value(self.passport_number, 3)

    @property
    def other_id_masked(self):
        return mask_value(self.other_id_number, 3)


class BankDetail(TimeStampedModel):
    class Verification(models.TextChoices):
        UNVERIFIED = "UNVERIFIED", "Not verified"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Verification failed"

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="bank")
    bank_name = models.CharField(max_length=120)
    account_holder_name = models.CharField(max_length=150)
    account_number = models.CharField(max_length=20, validators=[account_validator])
    ifsc_code = models.CharField("IFSC", max_length=11, validators=[ifsc_validator])
    branch = models.CharField(max_length=120, blank=True)
    verification_status = models.CharField(max_length=12, choices=Verification.choices, default=Verification.UNVERIFIED)

    @property
    def account_masked(self):
        return mask_value(self.account_number, 4)


class FamilyMember(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="family_members")
    name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=40)
    date_of_birth = models.DateField(null=True, blank=True)
    contact_number = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    is_dependent = models.BooleanField(default=False)
    is_nominee = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]


class Qualification(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="qualifications")
    qualification = models.CharField(max_length=120)
    specialization = models.CharField(max_length=120, blank=True)
    institution = models.CharField(max_length=200)
    university_or_board = models.CharField(max_length=200, blank=True)
    passing_year = models.PositiveSmallIntegerField()
    score = models.CharField("Percentage / CGPA", max_length=20, blank=True)
    document = models.ForeignKey("EmployeeDocument", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-passing_year"]


class EmployeeDocument(TimeStampedModel):
    class Category(models.TextChoices):
        EMPLOYMENT = "EMPLOYMENT", "Employment"
        EDUCATION = "EDUCATION", "Educational"
        IDENTITY = "IDENTITY", "Identity"
        LEAVE_FORM = "LEAVE_FORM", "Signed leave form"
        OTHER = "OTHER", "Other HR document"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    category = models.CharField(max_length=12, choices=Category.choices)
    title = models.CharField(max_length=150)
    file = models.FileField(storage=private_storage, upload_to=upload_employee_document)
    original_name = models.CharField(max_length=200)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    visible_to_employee = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_restricted(self):
        return self.category == self.Category.IDENTITY
