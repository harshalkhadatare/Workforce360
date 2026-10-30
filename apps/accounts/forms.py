from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm

from apps.core.forms import StyledFormMixin

from . import security
from .models import User, normalize_employee_id

GENERIC_LOGIN_ERROR = "Invalid Employee ID or password."


class LoginForm(StyledFormMixin, forms.Form):
    employee_id = forms.CharField(label="Employee ID", max_length=30, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True, "autocapitalize": "characters"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember_me = forms.BooleanField(required=False, label="Keep me signed in on this device for 7 days")

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        from apps.core.middleware import client_ip

        cleaned = super().clean()
        eid = normalize_employee_id(cleaned.get("employee_id"))
        pwd = cleaned.get("password")
        if not eid or not pwd:
            return cleaned
        ip = client_ip(self.request)
        if security.is_locked(eid, ip):
            raise forms.ValidationError(
                f"Too many unsuccessful sign-in attempts. Please wait {settings.LOGIN_LOCKOUT_SECONDS // 60} minutes or reset your password.")
        user = authenticate(self.request, username=eid, password=pwd)
        if user is None:
            security.register_failure(eid, ip)
            # Same message whether the ID is unknown, the password is wrong or the account is disabled.
            raise forms.ValidationError(GENERIC_LOGIN_ERROR)
        security.clear_failures(eid, ip)
        self.user = user
        return cleaned


class EmployeeIdPasswordResetForm(StyledFormMixin, PasswordResetForm):
    """Password reset by Employee ID. Always shows the same confirmation page."""

    email = None
    employee_id = forms.CharField(label="Employee ID", max_length=30, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))
    field_order = ["employee_id"]

    def get_users(self, email=None):
        eid = normalize_employee_id(self.cleaned_data.get("employee_id"))
        return [u for u in User.objects.filter(employee_code=eid, is_active=True) if u.has_usable_password() or True]

    def save(self, domain_override=None, subject_template_name=None, email_template_name=None, use_https=False,
             token_generator=None, from_email=None, request=None, html_email_template_name=None, extra_email_context=None):
        from django.contrib.auth.tokens import default_token_generator
        from django.urls import reverse
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from apps.audit import services as audit
        from apps.notifications.services import send_transactional

        token_generator = token_generator or default_token_generator
        for user in self.get_users():
            if not user.email:
                audit.log(None, "account.password_reset_no_email", user)
                continue
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            link = settings.PORTAL_BASE_URL + reverse("accounts:password_reset_confirm", args=[uid, token_generator.make_token(user)])
            send_transactional(
                event_type="account.password_reset", recipient=user.email,
                subject=f"[{settings.COMPANY_SHORT_NAME} HR] Password reset",
                context={"heading": "Reset your password", "recipient_name": user.get_full_name(), "employee_id": user.employee_code,
                         "intro": f"We received a request to reset the password for Employee ID {user.employee_code}. "
                                  f"The link expires in {settings.PASSWORD_RESET_TIMEOUT // 60} minutes and can be used once. "
                                  "If you did not request this, you can ignore this email."},
                secret_context={"link": link}, template="emails/account_setup",
            )
            audit.log(None, "account.password_reset_requested", user)


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    pass
