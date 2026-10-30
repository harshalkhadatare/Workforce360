from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

employee_id_validator = RegexValidator(
    r"^[A-Za-z0-9][A-Za-z0-9\-/_.]{0,29}$",
    "Employee ID may contain letters, digits and - / _ . (max 30 characters).",
)


def normalize_employee_id(value):
    return (value or "").strip().upper()


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Administrator"
    MANAGER = "MANAGER", "Manager"
    EMPLOYEE = "EMPLOYEE", "Employee"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, employee_code, password=None, **extra):
        employee_code = normalize_employee_id(employee_code)
        if not employee_code:
            raise ValueError("Employee ID is required")
        email = extra.pop("email", "")
        user = self.model(employee_code=employee_code, email=self.normalize_email(email) if email else "", **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, employee_code, password=None, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(employee_code, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(employee_code=normalize_employee_id(username))


class User(AbstractBaseUser, PermissionsMixin):
    """Login account. The Employee ID is the username.

    Linked one-to-one to an Employee master record, so one employee can never
    have two login accounts (enforced by the unique OneToOne column).
    """

    employee_code = models.CharField("Employee ID", max_length=30, unique=True, validators=[employee_id_validator])
    full_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True, help_text="Official email used for notifications and password reset")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EMPLOYEE, db_index=True)
    employee = models.OneToOneField(
        "employees.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="user"
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can open the Django admin site (technical admins only)")
    date_joined = models.DateTimeField(default=timezone.now)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "employee_code"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["employee_code"]

    def __str__(self):
        return f"{self.employee_code} — {self.full_name}" if self.full_name else self.employee_code

    def save(self, *args, **kwargs):
        self.employee_code = normalize_employee_id(self.employee_code)
        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.full_name or self.employee_code

    def get_short_name(self):
        return (self.full_name or self.employee_code).split(" ")[0]

    # --- role helpers (authorisation is still checked server-side per object) ---
    @property
    def is_admin_role(self):
        return self.is_active and (self.role == Role.ADMIN or self.is_superuser)

    def in_group(self, name):
        return self.groups.filter(name=name).exists()

    @property
    def is_hr(self):
        return self.is_admin_role or self.in_group("HR")

    @property
    def is_finance(self):
        return self.is_admin_role or self.in_group("Finance")

    @property
    def has_team(self):
        return bool(self.employee_id and self.employee.direct_reports.filter(is_active=True).exists())

    @property
    def is_manager_role(self):
        return self.role == Role.MANAGER or self.has_team
