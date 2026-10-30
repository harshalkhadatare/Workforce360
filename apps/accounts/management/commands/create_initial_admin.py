"""Securely create the first administrator.

The password is prompted interactively (never echoed) or read from the
INITIAL_ADMIN_PASSWORD environment variable for automated provisioning.
No default password exists anywhere in the code base.
"""
import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.audit import services as audit


class Command(BaseCommand):
    help = "Create the first portal administrator (prompts for a password)."

    def add_arguments(self, parser):
        parser.add_argument("--employee-id", required=True, help="Login ID for the administrator, e.g. ADMIN01")
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--no-input", action="store_true", help="Read the password from INITIAL_ADMIN_PASSWORD")

    def handle(self, *args, **opts):
        eid = opts["employee_id"].strip().upper()
        if User.objects.filter(employee_code=eid).exists():
            raise CommandError(f"A user with Employee ID {eid} already exists.")
        if opts["no_input"]:
            password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
            if not password:
                raise CommandError("INITIAL_ADMIN_PASSWORD is not set.")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        user = User(employee_code=eid, full_name=opts["name"], email=opts["email"], role=Role.ADMIN, is_staff=True, is_superuser=True)
        try:
            validate_password(password, user)
        except ValidationError as e:
            raise CommandError("; ".join(e.messages))
        user.set_password(password)
        user.save()
        audit.log(None, "account.initial_admin_created", user)
        self.stdout.write(self.style.SUCCESS(f"Administrator {eid} created. Link it to an employee record in the portal if needed."))
