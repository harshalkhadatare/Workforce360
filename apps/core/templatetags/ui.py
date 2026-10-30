from django import template
from django.utils.html import format_html
from django.utils.http import urlencode

from apps.core.utils import mask_value, minutes_to_hhmm

register = template.Library()

STATUS_TONES = {
    # approvals
    "PENDING_MANAGER": "amber", "PENDING_HR": "amber", "PENDING_ADMIN": "amber", "NEEDS_INFORMATION": "blue",
    "APPROVED": "green", "REJECTED": "red", "CANCELLED": "gray",
    # attendance
    "PRESENT": "green", "HALF_DAY_FLAG": "amber", "ABSENT": "red", "INCOMPLETE": "amber", "LEAVE": "blue",
    "HALF_DAY_LEAVE": "blue", "ON_DUTY": "teal", "WEEKLY_OFF": "gray", "HOLIDAY": "violet", "NOT_IMPORTED": "gray",
    # imports
    "UPLOADED": "gray", "VALIDATED": "blue", "COMPLETED": "green", "PARTIAL": "amber", "FAILED": "red", "ROLLED_BACK": "gray",
    "DISCARDED": "gray", "VALID": "green", "WARNING": "amber", "ERROR": "red", "DUPLICATE": "gray", "CONFLICT": "violet",
    "IMPORTED": "green", "RECON_OK": "green", "RECON_NO": "gray",
    # queries
    "OPEN": "blue", "ASSIGNED": "blue", "IN_PROGRESS": "amber", "AWAITING_EMP": "violet", "RESOLVED": "green", "CLOSED": "gray",
    # email
    "QUEUED": "gray", "SENT": "green", "RETRYING": "amber", "NOT_CONFIGURED": "gray",
    # employees / policies
    "ACTIVE": "green", "INACTIVE": "gray", "EXITED": "gray", "ON_NOTICE": "amber", "DRAFT": "gray", "RETIRED": "gray",
    "VERIFIED": "green", "UNVERIFIED": "amber",
}


@register.simple_tag
def badge(value, label=None):
    tone = STATUS_TONES.get(str(value), "gray")
    return format_html('<span class="badge-status tone-{}">{}</span>', tone, label or str(value).replace("_", " ").title())


@register.filter
def hhmm(minutes):
    return minutes_to_hhmm(minutes)


@register.filter
def masked(value, visible=4):
    return mask_value(value, int(visible))


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Current query string with replacements, e.g. {% qs page=3 %}."""
    params = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    return "?" + urlencode(list(params.lists()), doseq=True) if params else "?"


@register.simple_tag(takes_context=True)
def nav_active(context, *prefixes):
    path = context["request"].path
    return "active" if any(path.startswith(p) for p in prefixes) else ""


@register.filter
def num(value):
    """Render Decimal without trailing zeros."""
    try:
        return format(value.normalize(), "f") if hasattr(value, "normalize") else value
    except Exception:  # noqa: BLE001
        return value
