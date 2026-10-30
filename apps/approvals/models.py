from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import TimeStampedModel


class Status(models.TextChoices):
    PENDING_MANAGER = "PENDING_MANAGER", "Pending manager"
    PENDING_HR = "PENDING_HR", "Pending HR"
    PENDING_ADMIN = "PENDING_ADMIN", "Pending admin"
    NEEDS_INFORMATION = "NEEDS_INFORMATION", "Needs information"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


PENDING_STATUSES = (Status.PENDING_MANAGER, Status.PENDING_HR, Status.PENDING_ADMIN)
OPEN_STATUSES = PENDING_STATUSES + (Status.NEEDS_INFORMATION,)
STEP_STATUS = {"MANAGER": Status.PENDING_MANAGER, "HR": Status.PENDING_HR, "ADMIN": Status.PENDING_ADMIN}


class ApprovalRequest(TimeStampedModel):
    """Abstract base for every request that goes through the approval workflow."""

    REQUEST_TYPE = "GENERIC"
    TYPE_LABEL = "Request"
    URL_SLUG = "generic"

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    chain = models.JSONField(default=list, help_text="Approval steps snapshot taken at submission")
    current_step = models.PositiveSmallIntegerField(default=0)
    assigned_manager = models.ForeignKey(
        "employees.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Reporting manager at submission time (employees cannot choose their approver)",
    )
    info_return_status = models.CharField(max_length=20, blank=True)
    submitted_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decision_comment = models.TextField(blank=True)

    class Meta:
        abstract = True

    # Hooks overridden by concrete requests; called inside the approval transaction.
    def workflow_type(self):
        return self.REQUEST_TYPE

    def on_approved(self, actor):
        pass

    def on_rejected(self, actor):
        pass

    def on_cancel_approved(self, actor, reason):
        pass

    def summary(self):
        return self.TYPE_LABEL

    def notification_details(self):
        return []

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def is_pending(self):
        return self.status in PENDING_STATUSES

    @property
    def current_step_name(self):
        if self.chain and self.current_step < len(self.chain):
            return self.chain[self.current_step]
        return ""

    def history(self):
        ct = ContentType.objects.get_for_model(self.__class__)
        return ApprovalAction.objects.filter(content_type=ct, object_id=self.pk).select_related("actor")


class ApprovalAction(models.Model):
    class Action(models.TextChoices):
        SUBMIT = "SUBMIT", "Submitted"
        APPROVE = "APPROVE", "Approved"
        FORWARD = "FORWARD", "Approved and forwarded"
        REJECT = "REJECT", "Rejected"
        REQUEST_INFO = "REQUEST_INFO", "Requested clarification"
        RESPOND = "RESPOND", "Employee responded"
        CANCEL = "CANCEL", "Cancelled"
        REMARK = "REMARK", "Remark added"
        SYSTEM = "SYSTEM", "System update"

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    request = GenericForeignKey("content_type", "object_id")
    request_type = models.CharField(max_length=30, db_index=True)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=15, choices=Action.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    step = models.CharField(max_length=10, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
