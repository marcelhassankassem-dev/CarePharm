from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import Batch, DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient

DEMO_PASSWORD = "carepharm123"
SUPPLIER = "Cedar Medical Supplies"


def get_or_create_user(username, role):
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return User.objects.get(username=username)
    return User.objects.create_user(username=username, password=DEMO_PASSWORD, role=role)


def create_staff():
    User = get_user_model()
    if not User.objects.filter(username="nadim").exists():
        User.objects.create_superuser(
            username="nadim",
            password=DEMO_PASSWORD,
            role="owner",
            is_pharmacist=True,
        )
    get_or_create_user("rana", "pharmacist")
    get_or_create_user("karim", "assistant")


def create_patient(name, date_of_birth, phone, user):
    Patient.objects.get_or_create(
        name=name,
        defaults={"date_of_birth": date_of_birth, "phone": phone, "user": user},
    )


def create_patients():
    samira = get_or_create_user("samira", "patient")
    maya = get_or_create_user("maya", "patient")
    omars_father = get_or_create_user("omars_father", "patient")
    get_or_create_user("omar", "patient")

    create_patient("Samira", date(1958, 3, 14), "03 100 001", samira)
    create_patient("Maya", date(1988, 9, 21), "03 100 002", maya)
    create_patient("Omar's Father", date(1949, 11, 5), "03 100 003", omars_father)
    create_patient("Ahmad", date(1980, 6, 2), "03 100 004", None)


def create_medicine(name, strength, barcode, drug_class_name, price, minimum_quantity):
    drug_class, created = DrugClass.objects.get_or_create(name=drug_class_name)
    medicine, created = Medicine.objects.get_or_create(
        barcode=barcode,
        defaults={
            "name": name,
            "strength": strength,
            "drug_class": drug_class,
            "price": Decimal(price),
            "minimum_quantity": minimum_quantity,
        },
    )
    return medicine


def create_batch(medicine, batch_number, days_until_expiry, quantity, unit_cost):
    Batch.objects.get_or_create(
        medicine=medicine,
        batch_number=batch_number,
        defaults={
            "expiry_date": timezone.localdate() + timedelta(days=days_until_expiry),
            "quantity": quantity,
            "unit_cost": Decimal(unit_cost),
            "supplier": SUPPLIER,
        },
    )


def create_stock():
    amoxicillin = create_medicine("Amoxicillin", "500 mg", "1000000000001", "Penicillins", "8.50", 20)
    create_batch(amoxicillin, "AMX-001", 365, 120, "4.25")

    ibuprofen = create_medicine("Ibuprofen", "400 mg", "1000000000002", "NSAIDs", "4.00", 30)
    create_batch(ibuprofen, "IBU-001", 300, 12, "2.00")

    atorvastatin = create_medicine("Atorvastatin", "20 mg", "1000000000003", "Statins", "12.00", 15)
    create_batch(atorvastatin, "ATV-001", 45, 40, "6.00")
    create_batch(atorvastatin, "ATV-002", 400, 60, "6.00")

    metformin = create_medicine("Metformin", "500 mg", "1000000000004", "Biguanides", "6.00", 25)
    create_batch(metformin, "MET-001", 500, 150, "3.00")

    paracetamol = create_medicine("Paracetamol", "500 mg", "1000000000005", "Analgesics", "3.00", 40)
    create_batch(paracetamol, "PAR-001", -20, 25, "1.50")
    create_batch(paracetamol, "PAR-002", 240, 200, "1.50")


def add_allergy(patient_name, drug_class_name):
    Allergy.objects.get_or_create(
        patient=Patient.objects.get(name=patient_name),
        drug_class=DrugClass.objects.get(name=drug_class_name),
    )


def add_current_medicine(patient_name, medicine_name):
    CurrentMedicine.objects.get_or_create(
        patient=Patient.objects.get(name=patient_name),
        medicine=Medicine.objects.get(name=medicine_name),
    )


def create_passports():
    add_allergy("Ahmad", "Penicillins")
    add_current_medicine("Ahmad", "Metformin")
    add_current_medicine("Samira", "Metformin")
    add_current_medicine("Samira", "Atorvastatin")


class Command(BaseCommand):
    def handle(self, *args, **options):
        create_staff()
        create_patients()
        create_stock()
        create_passports()
