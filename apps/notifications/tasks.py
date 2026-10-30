from celery import shared_task


@shared_task(name="apps.notifications.tasks.send_email_task")
def send_email_task(log_id):
    from .services import deliver

    deliver(log_id)


@shared_task(name="apps.notifications.tasks.retry_failed_emails")
def retry_failed_emails():
    from .services import retry_due_emails

    return retry_due_emails()


@shared_task(name="apps.notifications.tasks.flag_overdue_emergency_forms")
def flag_overdue_emergency_forms():
    from apps.leave_management.services import flag_overdue_emergency_forms as run

    return len(run())
