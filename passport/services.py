from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404

from accounts.services import create_patient_login
from core.services import unread_count
from passport.models import Allergy, CurrentMedicine, Patient


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
        patients = patients.filter(Q(name__icontains=search) | Q(phone__icontains=search))
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
