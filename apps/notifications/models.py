from django.db import models

from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    """In-portal notification for a user."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="notifications")
    event_type = models.CharField(max_length=60)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    dedupe_key = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["-created_at"]


class EmailDeliveryLog(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENT = "SENT", "Accepted by mail server"
        RETRYING = "RETRYING", "Failed — will retry"
        FAILED = "FAILED", "Failed permanently"
        NOT_CONFIGURED = "NOT_CONFIGURED", "Not sent — email not configured"

    class Role(models.TextChoices):
        TO = "TO", "To"
        CC = "CC", "Cc"

    event_type = models.CharField(max_length=60, db_index=True)
    related_type = models.CharField(max_length=60, blank=True)
    related_id = models.CharField(max_length=40, blank=True)
    recipient = models.EmailField()
    recipient_role = models.CharField(max_length=2, choices=Role.choices, default=Role.TO)
    cc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=255)
    template = models.CharField(max_length=100)
    context = models.JSONField(default=dict, blank=True, help_text="Non-secret rendering context (no tokens/passwords)")
    attempts = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.QUEUED, db_index=True)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=220, unique=True, help_text="Prevents duplicate emails for the same event")

    class Meta:
        ordering = ["-created_at"]


class RecipientGroup(TimeStampedModel):
    """Configurable distribution lists such as HR and Management."""

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    emails = models.TextField(blank=True, help_text="One address per line")
    members = models.ManyToManyField("accounts.User", blank=True, related_name="recipient_groups")
    is_active = models.BooleanField(default=True)

    def addresses(self):
        out = [e.strip() for e in self.emails.splitlines() if e.strip()]
        out += [u.email for u in self.members.filter(is_active=True) if u.email]
        return sorted(set(out))

    def __str__(self):
        return self.name


class NotificationSettings(models.Model):
    """Singleton for NON-secret email settings. SMTP credentials stay in the environment."""

    from_name = models.CharField(max_length=100, default="HR Portal")
    helpdesk_email = models.EmailField(blank=True)
    helpdesk_phone = models.CharField(max_length=30, blank=True)
    email_notifications_enabled = models.BooleanField(default=True)
    short_leave_notify_team = models.BooleanField(default=True, help_text="Attendance policy: notify reporting manager and team")
    short_leave_cc_groups = models.ManyToManyField(RecipientGroup, blank=True, related_name="+", help_text="Policy: Management and HR in CC")
    import_notify_groups = models.ManyToManyField(RecipientGroup, blank=True, related_name="+")
    query_notify_groups = models.ManyToManyField(RecipientGroup, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Notification settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
