from accounts.permissions import is_pharmacy_staff


def sign_in_problem(user, side):
    if user is None:
        return "Wrong username or password."
    if side == "staff" and not is_pharmacy_staff(user):
        return "This is not a staff account."
    if side == "patient" and is_pharmacy_staff(user):
        return "This is not a patient account."
    return ""
