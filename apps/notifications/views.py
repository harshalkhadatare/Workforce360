from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.audit import services as audit
from apps.core.forms import StyledForm, StyledModelForm
from apps.core.permissions import admin_required, hr_required
from apps.core.utils import paginate

from . import services
from .models import EmailDeliveryLog, Notification, NotificationSettings, RecipientGroup


class SettingsForm(StyledModelForm):
    class Meta:
        model = NotificationSettings
        fields = ["from_name", "helpdesk_email", "helpdesk_phone", "email_notifications_enabled", "short_leave_notify_team",
                  "short_leave_cc_groups", "import_notify_groups", "query_notify_groups"]
        widgets = {"short_leave_cc_groups": forms.CheckboxSelectMultiple, "import_notify_groups": forms.CheckboxSelectMultiple,
                   "query_notify_groups": forms.CheckboxSelectMultiple}


class GroupForm(StyledModelForm):
    class Meta:
        model = RecipientGroup
        fields = ["code", "name", "emails", "is_active"]
        widgets = {"emails": forms.Textarea(attrs={"rows": 3})}


class AnnouncementForm(StyledForm):
    title = forms.CharField(max_length=150)
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    send_email = forms.BooleanField(required=False, initial=True, label="Also send by email")


@login_required
def notification_list(request):
    qs = request.user.notifications.all()
    if request.method == "POST":
        if request.POST.get("all"):
            qs.filter(is_read=False).update(is_read=True)
        elif request.POST.get("id"):
            n = get_object_or_404(Notification, pk=request.POST["id"], user=request.user)
            n.is_read = True
            n.save(update_fields=["is_read"])
            if n.link and n.link.startswith("/") and not n.link.startswith("//"):
                return redirect(n.link)
        return redirect("notifications:list")
    show = request.GET.get("show")
    if show == "unread":
        qs = qs.filter(is_read=False)
    return render(request, "notifications/list.html", {"page_obj": paginate(request, qs), "show": show,
                                                       "emails": EmailDeliveryLog.objects.filter(recipient__iexact=request.user.email).order_by("-created_at")[:20]
                                                       if request.user.email else []})


@hr_required
def settings_page(request):
    ns = NotificationSettings.load()
    form = SettingsForm(request.POST or None, instance=ns, prefix="s")
    gform = GroupForm(request.POST or None, prefix="g")
    aform = AnnouncementForm(request.POST or None, prefix="a")
    action = request.POST.get("action")
    if request.method == "POST":
        if action == "settings" and form.is_valid():
            form.save()
            audit.log(request.user, "notifications.settings_updated", ns, after={k: str(v) for k, v in form.cleaned_data.items()})
            messages.success(request, "Notification settings saved.")
            return redirect("notifications:settings")
        if action == "group" and gform.is_valid():
            g = gform.save()
            audit.log(request.user, "notifications.group_saved", g, after={"code": g.code})
            messages.success(request, f"Recipient group {g.name} saved.")
            return redirect("notifications:settings")
        if action == "test":
            to = request.user.email
            if not to:
                messages.error(request, "Your account has no email address to send a test to.")
            else:
                entry = services.queue_email(event_type="system.test", recipient=to, subject=f"[{settings.COMPANY_SHORT_NAME} HR] Test email",
                                             context={"heading": "Test email", "intro": "This is a test message from the HR portal.",
                                                      "recipient_name": request.user.get_full_name()},
                                             dedupe_key=f"test:{request.user.pk}:{timezone.now().timestamp()}")
                entry.refresh_from_db()
                messages.info(request, f"Test email to {to}: {entry.get_status_display()}{' — ' + entry.last_error if entry.last_error else ''}")
            return redirect("notifications:settings")
        if action == "announce" and aform.is_valid():
            d = aform.cleaned_data
            stamp = timezone.now().strftime("%Y%m%d%H%M%S")
            users = User.objects.filter(is_active=True)
            services.notify_users(users, event_type="announcement", title=d["title"], intro=d["message"], dedupe=f"announce:{stamp}",
                                  email=d["send_email"])
            audit.log(request.user, "notifications.announcement", None, target_type="announcement", target_id=stamp, after={"title": d["title"]})
            messages.success(request, f"Announcement sent to {users.count()} users.")
            return redirect("notifications:settings")
    smtp = {"enabled": settings.EMAIL_ENABLED, "host": settings.EMAIL_HOST or "(not set)", "port": settings.EMAIL_PORT,
            "tls": settings.EMAIL_USE_TLS, "ssl": settings.EMAIL_USE_SSL, "user_set": bool(settings.EMAIL_HOST_USER),
            "password_set": bool(settings.EMAIL_HOST_PASSWORD), "from": settings.DEFAULT_FROM_EMAIL, "backend": settings.EMAIL_BACKEND.rsplit(".", 2)[-2],
            "celery": settings.USE_CELERY}
    return render(request, "notifications/settings.html", {"form": form, "gform": gform, "aform": aform, "smtp": smtp,
                                                           "groups": RecipientGroup.objects.all()})


@hr_required
def email_logs(request):
    qs = EmailDeliveryLog.objects.all()
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("q"):
        qs = qs.filter(recipient__icontains=request.GET["q"]) | qs.filter(event_type__icontains=request.GET["q"])
    return render(request, "notifications/email_logs.html", {"page_obj": paginate(request, qs.order_by("-created_at"), 50),
                                                             "statuses": EmailDeliveryLog.Status.choices})


@admin_required
@require_POST
def retry_email(request, pk):
    entry = get_object_or_404(EmailDeliveryLog, pk=pk)
    if entry.status in (EmailDeliveryLog.Status.FAILED, EmailDeliveryLog.Status.RETRYING, EmailDeliveryLog.Status.NOT_CONFIGURED):
        entry.status = EmailDeliveryLog.Status.QUEUED
        entry.save(update_fields=["status"])
        entry = services.deliver(entry.pk)
        audit.log(request.user, "email.manual_retry", entry)
        messages.info(request, f"Retry result: {entry.get_status_display()}")
    return redirect("notifications:email_logs")
