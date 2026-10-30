from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.audit import services as audit

from .forms import EmployeeIdPasswordResetForm, LoginForm, StyledPasswordChangeForm, StyledSetPasswordForm


@sensitive_post_parameters("password")
@csrf_protect
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = LoginForm(request, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.user
        login(request, user)  # rotates the session key (prevents fixation)
        remember = form.cleaned_data.get("remember_me")
        request.session["remember_me"] = bool(remember)
        request.session.set_expiry(None if remember else 0)  # 0 = until the browser closes (+ idle timeout)
        request.session["last_activity"] = int(timezone.now().timestamp())
        audit.log(user, "account.login", user)
        nxt = request.GET.get("next") or request.POST.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(nxt)
        return redirect("core:home")
    return render(request, "authentication/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        audit.log(request.user, "account.logout", request.user)
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("accounts:login")


class PasswordResetView(auth_views.PasswordResetView):
    form_class = EmployeeIdPasswordResetForm
    template_name = "authentication/password_reset_form.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "authentication/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = StyledSetPasswordForm
    template_name = "authentication/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        resp = super().form_valid(form)
        user = form.user
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password_changed_at"])
        from . import security

        security.clear_failures(user.employee_code)
        audit.log(user, "account.password_reset_completed", user)
        return resp


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "authentication/password_reset_complete.html"


@login_required
@sensitive_post_parameters()
def password_change(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password_changed_at"])
        update_session_auth_hash(request, user)
        audit.log(user, "account.password_changed", user)
        messages.success(request, "Your password has been changed.")
        return redirect("core:home")
    return render(request, "authentication/password_change.html", {"form": form})
