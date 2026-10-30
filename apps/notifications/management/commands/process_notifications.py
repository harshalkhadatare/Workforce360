from django.core.management.base import BaseCommand

from apps.leave_management.services import flag_overdue_emergency_forms
from apps.notifications.services import retry_due_emails


class Command(BaseCommand):
    help = "Retry failed emails that are due and flag overdue emergency leave forms. Run every 10 minutes via cron if Celery is not used."

    def handle(self, *args, **opts):
        n = retry_due_emails()
        flagged = flag_overdue_emergency_forms()
        self.stdout.write(f"Retried {n} email(s); flagged {len(flagged)} overdue emergency form(s).")
