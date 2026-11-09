STAFF_ROLES = ["pharmacist", "assistant", "owner"]
ASSIGNABLE_STAFF_ROLES = ["pharmacist", "assistant"]


def is_pharmacy_staff(user):
    return user.role in STAFF_ROLES


def is_owner(user):
    return user.role == "owner"


def can_act_as_pharmacist(user):
    return user.role == "pharmacist" or (is_owner(user) and user.is_pharmacist)
