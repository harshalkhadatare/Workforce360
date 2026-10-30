from .base import *  # noqa: F401,F403

SECRET_KEY = "test-only-secret-key-not-for-any-real-deployment-0123456789"
DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_ENABLED = True
USE_CELERY = False
CELERY_TASK_ALWAYS_EAGER = True
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

PRIVATE_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="hrms-test-media-"))
MEDIA_ROOT = PRIVATE_MEDIA_ROOT
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
