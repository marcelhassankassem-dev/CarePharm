from django.core.exceptions import PermissionDenied

STAFF_ROLES = ["pharmacist", "assistant", "owner"]
ASSIGNABLE_STAFF_ROLES = ["pharmacist", "assistant"]

ROLE_PERMISSIONS = [
    ("Record prescriptions", [True, True, False]),
    ("Continue past a safety warning", [True, True, False]),
    ("Record deliveries and fix stock", [True, True, True]),
    ("Make sales", [True, True, True]),
    ("Reply to AI handovers", [True, True, False]),
    ("See reports", [True, False, False]),
    ("Manage staff accounts", [True, False, False]),
]


def is_pharmacy_staff(user):
    return user.role in STAFF_ROLES


def is_owner(user):
    return user.role == "owner"


def can_act_as_pharmacist(user):
    return user.role == "pharmacist" or (is_owner(user) and user.is_pharmacist)


def require_owner(user):
    if not is_owner(user):
        raise PermissionDenied
