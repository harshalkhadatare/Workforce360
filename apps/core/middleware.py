import contextvars
import time
import uuid

from django.conf import settings
from django.contrib.auth import logout
from django.contrib import messages
from django.shortcuts import redirect

_request_id = contextvars.ContextVar("request_id", default="")
_client_ip = contextvars.ContextVar("client_ip", default="")


def current_request_id():
    return _request_id.get()


def current_client_ip():
    return _client_ip.get()


def client_ip(request):
    # Behind Nginx the real address is passed in X-Real-IP (see deploy/nginx.conf).
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "")


class RequestIDMiddleware:
    """Assigns a correlation ID to each request (used by the audit log)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = uuid.uuid4().hex[:16]
        request.request_id = rid
        t1 = _request_id.set(rid)
        t2 = _client_ip.set(client_ip(request))
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(t1)
            _client_ip.reset(t2)
        response["X-Request-ID"] = rid
        return response


class SessionIdleTimeoutMiddleware:
    """Ends sessions that were idle longer than SESSION_IDLE_TIMEOUT_SECONDS.

    Sessions created with "keep me signed in" are exempt from the idle timeout
    but still bounded by SESSION_COOKIE_AGE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.session.get("remember_me"):
            now = int(time.time())
            last = request.session.get("last_activity")
            if last and now - last > settings.SESSION_IDLE_TIMEOUT_SECONDS:
                logout(request)
                messages.info(request, "Your session expired after a period of inactivity. Please sign in again.")
                return redirect(settings.LOGIN_URL)
            request.session["last_activity"] = now
        return self.get_response(request)
