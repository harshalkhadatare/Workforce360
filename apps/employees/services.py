from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import Role, User, normalize_employee_id
from apps.audit import services as audit

from .models import Employee, EmploymentRecord, ReportingRelationship

TRACKED_FIELDS = ["department", "designation", "reporting_manager", "employment_type", "employment_status", "probation_status",
                  "confirmation_date", "work_location", "work_schedule", "exit_date", "date_of_joining"]


def _display(value):
    if value is None or value == "":
        return ""
    return str(value)


def snapshot(emp, fields=None):
    out = {}
    for f in fields or TRACKED_FIELDS:
        val = getattr(emp, f)
        out[f] = _display(val)
    return out


def check_manager_cycle(emp, manager):
    seen = set()
    cur = manager
    while cur is not None:
        if cur.pk == emp.pk:
            raise ValidationError({"reporting_manager": "This assignment would create a reporting loop."})
        if cur.pk in seen:
            break
        seen.add(cur.pk)
        cur = cur.reporting_manager


@transaction.atomic
def save_employee(emp, actor, *, reason="", effective_date=None, is_new=False):
    """Persist an Employee, recording employment-history rows and the audit trail."""
    effective_date = effective_date or timezone.localdate()
    emp.employee_id = normalize_employee_id(emp.employee_id)
    if emp.reporting_manager_id:
        check_manager_cycle(emp, emp.reporting_manager)
    old = None
    if not is_new and emp.pk:
        old = Employee.objects.select_for_update().get(pk=emp.pk)
        if old.confirmation_date != emp.confirmation_date and not reason:
            raise ValidationError({"confirmation_date": "Use the confirmation action to set or change the confirmation date."})
    emp.full_clean()
    emp.save()
    if old is None:
        if emp.reporting_manager_id:
            ReportingRelationship.objects.create(employee=emp, manager=emp.reporting_manager, start_date=emp.date_of_joining, changed_by=actor)
        audit.log(actor, "employee.created", emp, after=snapshot(emp))
        return emp
    before, after = audit.diff(snapshot(old), snapshot(emp))
    for field in before:
        EmploymentRecord.objects.create(employee=emp, field=field, old_value=before[field][:255], new_value=after[field][:255],
                                        effective_date=effective_date, reason=reason[:255], changed_by=actor)
    if old.reporting_manager_id != emp.reporting_manager_id:
        ReportingRelationship.objects.filter(employee=emp, end_date__isnull=True).update(end_date=effective_date)
        if emp.reporting_manager_id:
            ReportingRelationship.objects.create(employee=emp, manager=emp.reporting_manager, start_date=effective_date, changed_by=actor)
    if before:
        audit.log(actor, "employee.updated", emp, before=before, after=after, reason=reason)
    return emp


@transaction.atomic
def confirm_employee(emp, confirmation_date, actor, reason):
    """Authorised confirmation action — the only way an employee becomes 'confirmed'."""
    if not reason.strip():
        raise ValidationError("A reason / reference is required for confirmation.")
    if confirmation_date < emp.date_of_joining:
        raise ValidationError("Confirmation date cannot be before the joining date.")
    old = snapshot(emp, ["confirmation_date", "probation_status"])
    emp.confirmation_date = confirmation_date
    emp.probation_status = Employee.Probation.CONFIRMED
    emp.save(update_fields=["confirmation_date", "probation_status", "updated_at"])
    for f in ["confirmation_date", "probation_status"]:
        EmploymentRecord.objects.create(employee=emp, field=f, old_value=old[f], new_value=_display(getattr(emp, f)),
                                        effective_date=confirmation_date, reason=reason[:255], changed_by=actor)
    audit.log(actor, "employee.confirmed", emp, before=old, after=snapshot(emp, ["confirmation_date", "probation_status"]), reason=reason)
    return emp


@transaction.atomic
def set_active(emp, active, actor, reason, effective_date=None):
    if not reason.strip():
        raise ValidationError("A reason is required.")
    if not active and emp.direct_reports.filter(is_active=True).exists():
        raise ValidationError("Reassign this employee's direct reports before deactivating.")
    emp.is_active = active
    if not active:
        emp.employment_status = Employee.Status.EXITED if effective_date else Employee.Status.INACTIVE
        emp.exit_date = effective_date or emp.exit_date
    else:
        emp.employment_status = Employee.Status.ACTIVE
        emp.exit_date = None
    emp.save()
    user = getattr(emp, "user", None)
    if user:
        user.is_active = active
        user.save(update_fields=["is_active"])
    EmploymentRecord.objects.create(employee=emp, field="is_active", old_value=str(not active), new_value=str(active),
                                    effective_date=effective_date or timezone.localdate(), reason=reason[:255], changed_by=actor)
    audit.log(actor, "employee.reactivated" if active else "employee.deactivated", emp, reason=reason)
    return emp


@transaction.atomic
def create_login(emp, actor, role=Role.EMPLOYEE, send_setup_email=True):
    if hasattr(emp, "user"):
        raise ValidationError("This employee already has a login account.")
    if User.objects.filter(employee_code=emp.employee_id).exists():
        raise ValidationError("A login with this Employee ID already exists.")
    user = User.objects.create_user(emp.employee_id, None, full_name=emp.full_name, email=emp.official_email, role=role, employee=emp)
    audit.log(actor, "account.created", user, employee_ref=emp.employee_id, after={"role": role})
    if send_setup_email:
        transaction.on_commit(lambda: send_account_setup_email(user))
    return user


def password_setup_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.PORTAL_BASE_URL}{reverse('accounts:password_reset_confirm', args=[uid, token])}"


def send_account_setup_email(user):
    from apps.notifications.services import notify_in_app, send_transactional

    if not user.email:
        return None
    notify_in_app(user, "account.created", "Welcome to the HR portal", "Set your password using the link sent to your official email.",
                  "/", f"account-created:{user.pk}")
    return send_transactional(
        event_type="account.created", recipient=user.email, subject=f"[{settings.COMPANY_SHORT_NAME} HR] Your HR portal account",
        context={"heading": "Your HR portal account is ready", "recipient_name": user.get_full_name(), "employee_id": user.employee_code,
                 "intro": "An account has been created for you. Use the secure link below to set your password. "
                          f"The link expires in {settings.PASSWORD_RESET_TIMEOUT // 3600} hour(s) and works once."},
        secret_context={"link": password_setup_link(user)}, template="emails/account_setup",
    )


@transaction.atomic
def change_role(user, role, actor, reason=""):
    if user.pk == actor.pk and role != Role.ADMIN:
        raise ValidationError("You cannot remove your own administrator role.")
    old = user.role
    user.role = role
    user.is_staff = user.is_staff if role == Role.ADMIN else False
    user.save(update_fields=["role", "is_staff"])
    audit.log(actor, "account.role_changed", user, before={"role": old}, after={"role": role}, reason=reason)
    return user
