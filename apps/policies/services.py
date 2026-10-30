import datetime

from django.db.models import Q

from .defaults import DEFAULT_APPROVAL_CHAINS
from .models import ApprovalChainConfig, Holiday, PolicyVersion, WorkSchedule


class PolicyNotConfigured(Exception):
    pass


def get_policy(kind, on_date, employee=None):
    """Return the ACTIVE policy version effective on `on_date`.

    A department-specific version wins over a company-wide one with the same
    or earlier effective date.
    """
    qs = PolicyVersion.objects.filter(kind=kind, status=PolicyVersion.Status.ACTIVE, effective_from__lte=on_date)
    if employee is not None and employee.department_id:
        dept = qs.filter(departments=employee.department_id).order_by("-effective_from", "-id").first()
        if dept:
            return dept
    version = qs.filter(departments__isnull=True).order_by("-effective_from", "-id").first()
    if version is None:
        raise PolicyNotConfigured(f"No active {kind.lower()} policy is effective on {on_date:%d %b %Y}.")
    return version


def get_schedule(employee, on_date):
    code = None
    if employee is not None and employee.work_schedule_id:
        code = employee.work_schedule.code
    qs = WorkSchedule.objects.filter(effective_from__lte=on_date)
    sched = None
    if code:
        sched = qs.filter(code=code).order_by("-effective_from").first()
    if sched is None:
        default_code = WorkSchedule.objects.filter(is_default=True).values_list("code", flat=True).first()
        if default_code:
            sched = qs.filter(code=default_code).order_by("-effective_from").first()
    if sched is None:
        raise PolicyNotConfigured(f"No work schedule is effective on {on_date:%d %b %Y}.")
    return sched


def holidays_between(start, end, location=""):
    qs = Holiday.objects.filter(is_active=True, date__gte=start, date__lte=end)
    qs = qs.filter(Q(location="") | Q(location__iexact=location or ""))
    return {h.date: h for h in qs}


def is_weekly_off(employee, d):
    try:
        return d.weekday() in (get_schedule(employee, d).weekly_off_days or [])
    except PolicyNotConfigured:
        return False


def is_non_working_day(employee, d, holiday_map=None):
    if holiday_map is None:
        holiday_map = holidays_between(d, d, getattr(employee, "work_location", ""))
    return is_weekly_off(employee, d) or d in holiday_map


def approval_chain(request_type):
    cfg = ApprovalChainConfig.objects.filter(request_type=request_type).first()
    steps = cfg.steps if cfg and cfg.steps else DEFAULT_APPROVAL_CHAINS.get(request_type, ["MANAGER"])
    return list(steps)


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)
