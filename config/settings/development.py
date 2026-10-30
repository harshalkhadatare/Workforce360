from .base import *  # noqa: F401,F403
from .base import env, env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = SECRET_KEY or "dev-only-insecure-key-do-not-use-in-production"  # noqa: F405
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1"]  # noqa: F405

# Without SMTP configured, emails go to the console AND are logged as
# NOT_CONFIGURED (never "sent").
if not env("EMAIL_HOST", ""):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
