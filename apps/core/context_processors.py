from django.conf import settings

from . import permissions as perms


def portal(request):
    user = request.user
    ctx = {
        "COMPANY_NAME": settings.COMPANY_NAME,
        "COMPANY_SHORT_NAME": settings.COMPANY_SHORT_NAME,
    }
    if user.is_authenticated:
        ctx.update(
            {
                "nav_is_admin": perms.is_admin(user),
                "nav_is_hr": perms.is_hr(user),
                "nav_is_manager": perms.is_manager(user),
                "nav_is_finance": perms.is_finance(user),
                "nav_has_employee": perms.own_employee(user) is not None,
                "nav_unread_notifications": user.notifications.filter(is_read=False).count(),
            }
        )
    return ctx
