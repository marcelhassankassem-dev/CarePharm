import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.services import create_patient_login
from core.services import unread_count
from passport.models import Allergy, CurrentMedicine, PassportShare, Patient
from sales.services import dispensing_history

SHARE_DAYS = (1, 3, 7, 14, 30)


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


def passport_medicines(patient, next_refills):
    return [
        {"medicine": current.medicine, "next_refill": next_refills.get(current.medicine_id)}
        for current in current_medicines_of(patient)
    ]


def age_of(patient, today=None):
    today = today or timezone.localdate()
    born = patient.date_of_birth
    birthday_not_reached = (today.month, today.day) < (born.month, born.day)
    return today.year - born.year - birthday_not_reached


def share_passport(patient, pharmacy_name, sections, days):
    if not sections:
        raise ValidationError("Choose at least one section to share.")
    if days not in SHARE_DAYS:
        raise ValidationError("Choose how many days the pharmacy can see your passport.")
    return PassportShare.objects.create(
        patient=patient,
        token=secrets.token_urlsafe(24),
        pharmacy_name=pharmacy_name,
        include_contact="contact" in sections,
        include_allergies="allergies" in sections,
        include_medicines="medicines" in sections,
        include_history="history" in sections,
        expires_at=timezone.now() + timedelta(days=days),
    )


def share_is_open(share):
    return not share.revoked and timezone.now() < share.expires_at


def share_status(share):
    if share.revoked:
        return "Removed"
    if share_is_open(share):
        return "Active"
    return "Expired"


def shares_of(patient):
    return PassportShare.objects.filter(patient=patient).order_by("-created_at", "-id")


def share_rows(patient):
    return [{"share": share, "status": share_status(share)} for share in shares_of(patient)]


def open_shares_of(patient):
    return [share for share in shares_of(patient) if share_is_open(share)]


def get_share(patient, share_id):
    return get_object_or_404(PassportShare, pk=share_id, patient=patient)


def remove_share(share):
    share.revoked = True
    share.save()


def shared_passport(token):
    return get_object_or_404(PassportShare.objects.select_related("patient"), token=token)


def shared_sections(share):
    if not share_is_open(share):
        return {"is_open": False}
    patient = share.patient
    return {
        "is_open": True,
        "contact": share.include_contact,
        "allergies": allergies_of(patient) if share.include_allergies else None,
        "medicines": current_medicines_of(patient) if share.include_medicines else None,
        "history": dispensing_history(patient) if share.include_history else None,
    }
