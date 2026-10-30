import datetime
import decimal

from apps.core.middleware import current_client_ip, current_request_id

from .models import AuditLog

# Never written to the audit trail even if present in a diff.
REDACT_FIELDS = {"password", "token", "account_number", "aadhaar_number", "pan_number", "passport_number"}


def _jsonable(value):
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if hasattr(value, "pk"):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _clean(data):
    if data is None:
        return None
    out = {}
    for k, v in data.items():
        out[k] = "[redacted]" if k in REDACT_FIELDS else _jsonable(v)
    return out


def log(actor, action, target=None, *, reason="", before=None, after=None, employee_ref="", target_type=None, target_id=None, target_repr=None):
    actor_obj = actor if getattr(actor, "is_authenticated", False) else None
    if target is not None:
        target_type = target_type or f"{target._meta.app_label}.{target.__class__.__name__}"
        target_id = target_id or str(target.pk)
        target_repr = target_repr or str(target)[:255]
        if not employee_ref:
            emp = getattr(target, "employee", None)
            employee_ref = getattr(emp, "employee_id", "") or getattr(target, "employee_id_code", "") or ""
            if not employee_ref and target.__class__.__name__ == "Employee":
                employee_ref = target.employee_id
    return AuditLog.objects.create(
        actor=actor_obj,
        actor_label=(str(actor_obj.employee_code) if actor_obj else "system")[:120],
        action=action,
        target_type=target_type or "",
        target_id=target_id or "",
        target_repr=(target_repr or "")[:255],
        employee_ref=employee_ref or "",
        reason=reason or "",
        before=_clean(before),
        after=_clean(after),
        request_id=current_request_id(),
        ip_address=current_client_ip(),
    )


def diff(old: dict, new: dict):
    """Return (before, after) containing only changed keys."""
    keys = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}
