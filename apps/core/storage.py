"""Private file storage.

Files live under PRIVATE_MEDIA_ROOT (outside the static tree) and have no
public URL. They are streamed only by views that perform an authorisation
check. `private_storage` is a callable so migrations reference the function
rather than baking a filesystem path into migration files.
"""
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils import timezone

from .validators import safe_filename

_storage = None


def private_storage():
    global _storage
    if _storage is None or _storage.location != str(settings.PRIVATE_MEDIA_ROOT):
        _storage = FileSystemStorage(location=str(settings.PRIVATE_MEDIA_ROOT), base_url=None)
    return _storage


def _path(prefix, filename):
    d = timezone.now()
    return f"{prefix}/{d:%Y/%m}/{uuid.uuid4().hex}_{safe_filename(filename)}"


def upload_employee_document(instance, filename):
    return _path("employee_docs", filename)


def upload_request_attachment(instance, filename):
    return _path("request_attachments", filename)


def upload_query_attachment(instance, filename):
    return _path("query_attachments", filename)


def upload_import_file(instance, filename):
    return _path("imports", filename)


def upload_profile_photo(instance, filename):
    return _path("photos", filename)
