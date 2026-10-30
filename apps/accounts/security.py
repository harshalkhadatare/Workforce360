"""Login throttling.

Failures are counted per normalised Employee ID and per client IP in the
cache, regardless of whether the account exists, so lockout behaviour does not
reveal which Employee IDs are valid. Use Redis (REDIS_CACHE_URL) in production
so all Gunicorn workers share the counters.
"""
from django.conf import settings
from django.core.cache import cache

from .models import normalize_employee_id


def _keys(employee_id, ip):
    eid = normalize_employee_id(employee_id)
    return f"login-fail:id:{eid}", f"login-fail:ip:{ip}"


def is_locked(employee_id, ip):
    k_id, k_ip = _keys(employee_id, ip)
    return (cache.get(k_id, 0) >= settings.LOGIN_MAX_FAILURES) or (
        cache.get(k_ip, 0) >= settings.LOGIN_IP_MAX_FAILURES
    )


def register_failure(employee_id, ip):
    for key in _keys(employee_id, ip):
        try:
            cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def clear_failures(employee_id, ip=None):
    k_id, k_ip = _keys(employee_id, ip or "")
    cache.delete(k_id)
