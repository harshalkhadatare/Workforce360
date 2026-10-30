from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models


class AuditLog(models.Model):
    """Append-only audit trail. Rows cannot be updated or deleted via the ORM."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=120, blank=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80, blank=True, db_index=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    target_repr = models.CharField(max_length=255, blank=True)
    employee_ref = models.CharField(max_length=30, blank=True, db_index=True, help_text="Employee ID affected, if any")
    reason = models.TextField(blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    request_id = models.CharField(max_length=32, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action} {self.target_repr}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied("Audit records are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Audit records cannot be deleted")
