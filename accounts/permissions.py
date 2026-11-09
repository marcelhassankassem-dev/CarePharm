from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

STAFF_ROLES = ["pharmacist", "assistant", "owner"]


def is_pharmacy_staff(user):
    return user.role in STAFF_ROLES


def staff_required(view):
    @login_required(login_url="/login/staff/")
    def checked_view(request, *args, **kwargs):
        if not is_pharmacy_staff(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return checked_view
