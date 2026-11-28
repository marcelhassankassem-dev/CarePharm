from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.services import staff_members
from accounts.services import create_patient_login
from core.services import notify, unread_count
from passport.models import Allergy, CurrentMedicine, PassportShare, Patient


@transaction.atomic
def create_patient(
    name,
    date_of_birth,
    phone="",
    drug_classes=(),
    medicines=(),
    username="",
    password="",
):
    user = None
    if username:
        user = create_patient_login(username, password)
    patient = Patient.objects.create(
        name=name,
        date_of_birth=date_of_birth,
        phone=phone,
        user=user,
    )
    for drug_class in drug_classes:
        Allergy.objects.create(patient=patient, drug_class=drug_class)
    for medicine in medicines:
        CurrentMedicine.objects.create(patient=patient, medicine=medicine)
    return patient


def find_patients(search=""):
    patients = Patient.objects.annotate(allergy_count=Count("allergy")).order_by("name")
    if search:
        term = search.strip()
        conditions = Q(name__icontains=term) | Q(phone__icontains=term)
        if term.upper().startswith("CP-") and term[3:].isdigit():
            conditions |= Q(pk=int(term[3:]))
        patients = patients.filter(conditions)
    return patients


def get_patient(patient_id):
    return get_object_or_404(Patient, pk=patient_id)


def patient_of(user):
    return Patient.objects.filter(user=user).first()


def patients_cared_for(user):
    patients = Patient.objects.filter(caregiverlink__caregiver=user, caregiverlink__active=True)
    return patients.order_by("name")


def viewable_patients(user):
    own_record = patient_of(user)
    cared_for = list(patients_cared_for(user))
    if own_record:
        return [own_record] + cared_for
    return cared_for


def viewable_patient(user, patient_id):
    for patient in viewable_patients(user):
        if patient.pk == patient_id:
            return patient
    raise Http404


def chosen_patient(user, patient_id):
    patients = viewable_patients(user)
    for patient in patients:
        if patient.pk == patient_id:
            return patient
    if patients:
        return patients[0]
    return None


def patient_name_for(user):
    patient = patient_of(user)
    if patient:
        return patient.name
    return user.get_full_name() or user.username


def patient_header(user):
    return {"patient_name": patient_name_for(user), "unread_count": unread_count(user)}


def allergies_of(patient):
    return Allergy.objects.filter(patient=patient).select_related("drug_class")


def add_current_medicine(patient, medicine):
    current_medicine, created = CurrentMedicine.objects.get_or_create(patient=patient, medicine=medicine)
    return current_medicine


def current_medicines_of(patient):
    return CurrentMedicine.objects.filter(patient=patient).select_related("medicine")


def active_passport_share(patient):
    if patient is None:
        return None
    # Expiration is checked when staff requests the passport, so no background
    # job is needed. An older grant with no expiry must fail closed as well.
    return (
        PassportShare.objects.filter(
            patient=patient,
            pharmacy_name=settings.PHARMACY_NAME,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-consented_at", "-pk")
        .first()
    )


@transaction.atomic
def share_passport(
    patient, user, *, include_contact=False, include_allergies=False,
    include_medicines=False, include_history=False, access_days=7,
):
    if patient is None or patient.user_id != user.pk:
        raise PermissionDenied("Only the patient can consent to share their passport.")
    if not any((include_contact, include_allergies, include_medicines, include_history)):
        raise ValidationError("Select at least one section to share.")
    if access_days not in (1, 3, 7, 14, 30):
        raise ValidationError("Choose a valid access duration.")

    # Replacing a previous share must not leave its broader permissions active.
    now = timezone.now()
    PassportShare.objects.filter(patient=patient, revoked_at__isnull=True).update(revoked_at=now)
    share = PassportShare.objects.create(
        patient=patient,
        consented_by=user,
        pharmacy_name=settings.PHARMACY_NAME,
        include_contact=include_contact,
        include_allergies=include_allergies,
        include_medicines=include_medicines,
        include_history=include_history,
        expires_at=now + timedelta(days=access_days),
    )
    for staff in staff_members().filter(is_active=True):
        notify(staff, f"{patient.name} shared a medication passport with the pharmacy.", f"/patients/{patient.pk}/")
    return share


@transaction.atomic
def revoke_passport_share(patient, user, share_id):
    """Remove one consent grant without affecting a newer replacement grant."""
    if patient is None or patient.user_id != user.pk:
        raise PermissionDenied("Only the patient can remove pharmacy access to their passport.")

    share = get_object_or_404(
        PassportShare,
        pk=share_id,
        patient=patient,
        pharmacy_name=settings.PHARMACY_NAME,
    )
    if share.revoked_at is None:
        share.revoked_at = timezone.now()
        share.save(update_fields=["revoked_at"])
    return share
