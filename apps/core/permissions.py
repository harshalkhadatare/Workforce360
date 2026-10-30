"""Central, server-side authorisation rules.

Every view that loads an employee-owned object goes through these helpers.
Objects outside the user's scope raise 404 (not 403) so IDs cannot be probed.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_admin_role)


def is_hr(user):
    return bool(user and user.is_authenticated and user.is_hr)


def is_finance(user):
    return bool(user and user.is_authenticated and user.is_finance)


def is_manager(user):
    return bool(user and user.is_authenticated and (user.is_manager_role or is_admin(user)))


def own_employee(user):
    return getattr(user, "employee", None) if user.is_authenticated else None


def team_queryset(user):
    """Direct reports of the user (active and inactive)."""
    from apps.employees.models import Employee

    emp = own_employee(user)
    if not emp:
        return Employee.objects.none()
    return Employee.objects.filter(reporting_manager=emp)


def employee_scope(user):
    """Employees whose (non-sensitive) records the user may view."""
    from apps.employees.models import Employee

    if is_hr(user):
        return Employee.objects.all()
    emp = own_employee(user)
    if not emp:
        return Employee.objects.none()
    if is_manager(user):
        return Employee.objects.filter(pk=emp.pk) | Employee.objects.filter(reporting_manager=emp)
    return Employee.objects.filter(pk=emp.pk)


def can_view_employee(user, employee):
    return employee_scope(user).filter(pk=employee.pk).exists()


def can_edit_employee_master(user, employee=None):
    return is_hr(user)


def can_view_sensitive(user, employee):
    """Full (unmasked) identity and bank values: HR/Admin, or the employee themself."""
    if is_hr(user):
        return True
    emp = own_employee(user)
    return bool(emp and emp.pk == employee.pk)


def can_view_payroll(user, employee):
    return is_finance(user) or is_admin(user)


def is_reporting_manager_of(user, employee):
    emp = own_employee(user)
    return bool(emp and employee.reporting_manager_id == emp.pk)


def get_scoped_employee(user, pk):
    try:
        return employee_scope(user).distinct().get(pk=pk)
    except Exception:
        raise Http404("Employee not found")


def admin_required(view):
    @login_required
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not is_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return _wrapped


def hr_required(view):
    @login_required
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not is_hr(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return _wrapped


def manager_required(view):
    @login_required
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not is_manager(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return _wrapped


def employee_required(view):
    """Logged in AND linked to an employee record (needed for self-service)."""

    @login_required
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not own_employee(request.user):
            raise PermissionDenied("Your login is not linked to an employee record.")
        return view(request, *args, **kwargs)

    return _wrapped
