from .base import *  # noqa: F401,F403
from .base import env, env_bool, env_int

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
if len(SECRET_KEY) < 50:
    raise RuntimeError("DJANGO_SECRET_KEY must be at least 50 characters in production")

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
