from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Read-mostly technical view. Day-to-day account management happens in the portal (audited)."""

    list_display = ("employee_code", "full_name", "role", "is_active", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("employee_code", "full_name", "email")
    exclude = ("password",)
    readonly_fields = ("last_login", "date_joined", "password_changed_at")
