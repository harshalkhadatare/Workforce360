"""Upload validation: extension, size and file signature (magic bytes)."""
import hashlib
import os

from django.conf import settings
from django.core.exceptions import ValidationError

SIGNATURES = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".xlsx": [b"PK\x03\x04"],
}
DOCUMENT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def validate_upload(f, allowed=DOCUMENT_EXTENSIONS, max_bytes=None):
    if f is None:
        return
    max_bytes = max_bytes or settings.MAX_UPLOAD_BYTES
    name = os.path.basename(f.name or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in allowed:
        raise ValidationError(f"File type {ext or '(none)'} is not allowed. Allowed: {', '.join(allowed)}.")
    if f.size > max_bytes:
        raise ValidationError(f"File is too large. Maximum size is {max_bytes // (1024 * 1024)} MB.")
    if f.size == 0:
        raise ValidationError("The file is empty.")
    pos = f.tell() if hasattr(f, "tell") else 0
    f.seek(0)
    head = f.read(16)
    f.seek(pos)
    if not any(head.startswith(sig) for sig in SIGNATURES.get(ext, [])):
        raise ValidationError("The file content does not match its extension.")


def validate_document(f):
    validate_upload(f, DOCUMENT_EXTENSIONS)


def validate_image(f):
    validate_upload(f, IMAGE_EXTENSIONS)


def sha256_of(f):
    h = hashlib.sha256()
    f.seek(0)
    for chunk in iter(lambda: f.read(65536), b""):
        h.update(chunk)
    f.seek(0)
    return h.hexdigest()


def safe_filename(name):
    base = os.path.basename(name or "file")
    keep = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
    return keep[-120:] or "file"
