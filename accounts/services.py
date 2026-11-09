from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.permissions import is_pharmacy_staff


def sign_in_problem(user, side):
    if user is None:
        return "Wrong username or password."
    if side == "staff" and not is_pharmacy_staff(user):
        return "This is not a staff account."
    if side == "patient" and is_pharmacy_staff(user):
        return "This is not a patient account."
    return ""


def create_patient_login(username, password):
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        raise ValidationError("That username is already taken.")
    return User.objects.create_user(username=username, password=password, role="patient")
