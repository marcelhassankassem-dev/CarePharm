from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from accounts.permissions import ASSIGNABLE_STAFF_ROLES, STAFF_ROLES, is_pharmacy_staff


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


def staff_members():
    return get_user_model().objects.filter(role__in=STAFF_ROLES).order_by("id")


def active_staff_count():
    return staff_members().filter(is_active=True).count()


def create_staff_member(first_name, last_name, username, password, role):
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role=role,
    )


def editable_staff_member(member_id):
    return get_object_or_404(get_user_model(), pk=member_id, role__in=ASSIGNABLE_STAFF_ROLES)


def update_staff_member(member, role, is_active):
    member.role = role
    member.is_active = is_active
    member.save()
    return member
