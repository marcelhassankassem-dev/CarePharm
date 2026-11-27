from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from accounts.models import CaregiverLink
from accounts.permissions import ASSIGNABLE_STAFF_ROLES, STAFF_ROLES, is_owner, is_pharmacy_staff


def sign_in_problem(user, side):
    if user is None:
        return "Wrong username or password."
    if side == "staff" and not is_pharmacy_staff(user):
        return "This is not a staff account."
    if side == "patient" and is_pharmacy_staff(user):
        return "This is not a patient account."
    return ""


def landing_page(user):
    if is_owner(user):
        return "/staff/"
    if is_pharmacy_staff(user):
        return "/dashboard/"
    return "/home/"


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


def add_caregiver(patient, username):
    caregiver = get_user_model().objects.filter(username=username, role="patient").first()
    if caregiver is None:
        raise ValidationError(f"No patient account has the username {username}.")
    if caregiver == patient.user:
        raise ValidationError("You cannot add yourself.")
    link, created = CaregiverLink.objects.get_or_create(patient=patient, caregiver=caregiver)
    if link.active and not created:
        raise ValidationError(f"{username} can already manage your medicines.")
    link.active = True
    link.save()
    return link


def revoke_caregiver(patient, link_id):
    link = get_object_or_404(CaregiverLink, pk=link_id, patient=patient, active=True)
    link.active = False
    link.save()
    return link


def caregiver_links_of(patient):
    links = CaregiverLink.objects.filter(patient=patient, active=True).select_related("caregiver")
    return links.order_by("caregiver__username")
