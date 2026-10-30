"""Base settings shared by every environment.

All secrets and environment-specific values come from environment variables
(optionally loaded from a local .env file). Nothing secret is hardcoded here.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def env_bool(name, default=False):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

COMPANY_NAME = env("COMPANY_NAME", "Vision Infra Equipment Solutions Ltd.")
COMPANY_SHORT_NAME = env("COMPANY_SHORT_NAME", "Vision Infra")
PORTAL_BASE_URL = env("PORTAL_BASE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    # Project apps
    "apps.core",
    "apps.audit",
    "apps.accounts",
    "apps.employees",
    "apps.payroll",
    "apps.policies",
    "apps.approvals",
    "apps.attendance",
    "apps.leave_management",
    "apps.imports",
    "apps.notifications",
    "apps.queries",
    "apps.reporting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.SessionIdleTimeoutMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.portal",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — PostgreSQL is the production target.
# ---------------------------------------------------------------------------
DB_ENGINE = env("DB_ENGINE", "postgresql")
if DB_ENGINE == "sqlite":  # lightweight local development only
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "vision_hrms"),
            "USER": env("DB_USER", "vision_hrms"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "OPTIONS": {"sslmode": env("DB_SSLMODE", "prefer")},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Password reset links: time-limited (seconds) and single-use (token is bound
# to the password hash, so it stops working once the password changes).
PASSWORD_RESET_TIMEOUT = env_int("PASSWORD_RESET_TIMEOUT", 3600)

# Login throttling
LOGIN_MAX_FAILURES = env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCKOUT_SECONDS = env_int("LOGIN_LOCKOUT_SECONDS", 900)
LOGIN_IP_MAX_FAILURES = env_int("LOGIN_IP_MAX_FAILURES", 25)

# Sessions
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 24 * 7)  # max for "keep me signed in"
SESSION_IDLE_TIMEOUT_SECONDS = env_int("SESSION_IDLE_TIMEOUT_SECONDS", 30 * 60)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and private media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Private uploads live OUTSIDE the static tree and are never served by URL.
# They are streamed only by views that perform an authorisation check.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "media" / "private")))
MEDIA_ROOT = PRIVATE_MEDIA_ROOT  # no MEDIA_URL is mounted
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)
MAX_IMPORT_BYTES = env_int("MAX_IMPORT_BYTES", 10 * 1024 * 1024)
MAX_IMPORT_ROWS = env_int("MAX_IMPORT_ROWS", 50000)
FILE_UPLOAD_PERMISSIONS = 0o640
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# Email — credentials only from the environment.
# ---------------------------------------------------------------------------
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 20)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "HR Portal <hr-portal@example.invalid>")
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
# The portal only reports an email as "accepted by mail server" when this is
# True AND the backend returned success. Otherwise emails are logged as
# NOT_CONFIGURED, never as sent.
EMAIL_ENABLED = env_bool("EMAIL_ENABLED", bool(EMAIL_HOST))
EMAIL_MAX_ATTEMPTS = env_int("EMAIL_MAX_ATTEMPTS", 5)

# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
USE_CELERY = env_bool("USE_CELERY", False)
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "")
CELERY_TASK_ALWAYS_EAGER = not USE_CELERY
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "retry-failed-emails": {"task": "apps.notifications.tasks.retry_failed_emails", "schedule": 600.0},
}

REDIS_CACHE_URL = env("REDIS_CACHE_URL", "")
if REDIS_CACHE_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_CACHE_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "vision-hrms"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
