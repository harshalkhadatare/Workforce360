import mimetypes
import os

from django.core.paginator import Paginator
from django.http import FileResponse, Http404


def mask_value(value, visible=4, char="•"):
    """Mask all but the last `visible` characters of a sensitive value."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= visible:
        return char * len(s)
    return char * (len(s) - visible) + s[-visible:]


def paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


def private_file_response(field_file, download_name=None):
    if not field_file:
        raise Http404("No file")
    try:
        fh = field_file.open("rb")
    except FileNotFoundError:
        raise Http404("File missing")
    name = download_name or os.path.basename(field_file.name).split("_", 1)[-1]
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    resp = FileResponse(fh, as_attachment=True, filename=name, content_type=ctype)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


def minutes_to_hhmm(minutes):
    if minutes is None:
        return "—"
    minutes = int(minutes)
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60}h {minutes % 60:02d}m"
