STAFF_ROLES = ["pharmacist", "assistant", "owner"]


def is_pharmacy_staff(user):
    return user.role in STAFF_ROLES
