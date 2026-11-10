from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient
from prescriptions.models import Prescription
from prescriptions.services import (
    add_item,
    create_prescription,
    mark_collected,
    mark_ready,
    start_preparing,
)
from sales.models import Sale

DEMO_PASSWORD = "carepharm123"
SUPPLIER = "Cedar Medical Supplies"
DAYS_SINCE_DEMO_DELIVERIES = 30


def get_or_create_user(username, role, first_name="", last_name=""):
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return User.objects.get(username=username)
    return User.objects.create_user(
        username=username,
        password=DEMO_PASSWORD,
        role=role,
        first_name=first_name,
        last_name=last_name,
    )


def create_staff():
    User = get_user_model()
    if not User.objects.filter(username="nadim").exists():
        User.objects.create_superuser(
            username="nadim",
            password=DEMO_PASSWORD,
            role="owner",
            is_pharmacist=True,
            first_name="Nadim",
            last_name="Khoury",
        )
    get_or_create_user("rana", "pharmacist", "Rana", "Haddad")
    get_or_create_user("karim", "assistant", "Karim", "Saad")


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
    batch, created = Batch.objects.get_or_create(
        medicine=medicine,
        batch_number=batch_number,
        defaults={
            "expiry_date": timezone.localdate() + timedelta(days=days_until_expiry),
            "quantity": quantity,
            "unit_cost": Decimal(unit_cost),
            "supplier": SUPPLIER,
        },
    )
    if created:
        received_at = timezone.now() - timedelta(days=DAYS_SINCE_DEMO_DELIVERIES)
        Batch.objects.filter(pk=batch.pk).update(received_at=received_at)


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

    create_medicine("Metformin", "850 mg", "1000000000006", "Biguanides", "7.50", 25)

    amlodipine = create_medicine("Amlodipine", "5 mg", "1000000000007", "Calcium channel blockers", "9.00", 20)
    create_batch(amlodipine, "AML-001", 420, 90, "4.50")

    omeprazole = create_medicine("Omeprazole", "20 mg", "1000000000008", "Proton pump inhibitors", "7.00", 20)
    create_batch(omeprazole, "OME-001", 380, 80, "3.50")

    lisinopril = create_medicine("Lisinopril", "10 mg", "1000000000009", "ACE inhibitors", "8.00", 20)
    create_batch(lisinopril, "LIS-001", 450, 70, "4.00")

    losartan = create_medicine("Losartan", "50 mg", "1000000000010", "Angiotensin receptor blockers", "10.00", 15)
    create_batch(losartan, "LOS-001", 500, 60, "5.00")

    cetirizine = create_medicine("Cetirizine", "10 mg", "1000000000011", "Antihistamines", "5.00", 25)
    create_batch(cetirizine, "CET-001", 360, 100, "2.50")

    salbutamol = create_medicine("Salbutamol inhaler", "100 mcg", "1000000000012", "Bronchodilators", "11.00", 10)
    create_batch(salbutamol, "SAL-001", 400, 40, "5.50")

    levothyroxine = create_medicine("Levothyroxine", "50 mcg", "1000000000013", "Thyroid hormones", "6.50", 20)
    create_batch(levothyroxine, "LEV-001", 480, 75, "3.25")

    aspirin = create_medicine("Aspirin", "81 mg", "1000000000014", "NSAIDs", "3.50", 30)
    create_batch(aspirin, "ASP-001", 540, 130, "1.75")

    azithromycin = create_medicine("Azithromycin", "250 mg", "1000000000015", "Macrolides", "13.00", 15)
    create_batch(azithromycin, "AZI-001", 330, 50, "6.50")

    ampicillin = create_medicine("Ampicillin", "250 mg", "1000000000016", "Penicillins", "7.50", 15)
    create_batch(ampicillin, "AMP-001", 310, 55, "3.75")

    syrup = create_medicine("Paracetamol syrup", "120 mg/5 ml", "1000000000017", "Analgesics", "4.50", 20)
    create_batch(syrup, "SYR-001", 270, 85, "2.25")


def add_allergy(patient_name, drug_class_name):
    Allergy.objects.get_or_create(
        patient=Patient.objects.get(name=patient_name),
        drug_class=DrugClass.objects.get(name=drug_class_name),
    )


def add_current_medicine(patient_name, medicine_name, strength):
    CurrentMedicine.objects.get_or_create(
        patient=Patient.objects.get(name=patient_name),
        medicine=Medicine.objects.get(name=medicine_name, strength=strength),
    )


def create_passports():
    add_allergy("Ahmad", "Penicillins")
    add_current_medicine("Ahmad", "Metformin", "500 mg")
    add_current_medicine("Samira", "Metformin", "500 mg")
    add_current_medicine("Samira", "Atorvastatin", "20 mg")


def days_ago(days):
    return timezone.now() - timedelta(days=days)


def latest_log_id():
    latest = ActivityLog.objects.order_by("-id").first()
    return latest.pk if latest else 0


def move_log_entries_after(log_id, time):
    ActivityLog.objects.filter(pk__gt=log_id).update(time=time)


def demo_prescription(patient_name, medicine_name, strength, quantity, days_of_supply, long_term):
    prescription = create_prescription(Patient.objects.get(name=patient_name))
    medicine = Medicine.objects.get(name=medicine_name, strength=strength)
    add_item(prescription, medicine, quantity, days_of_supply, long_term)
    return prescription


def create_prescriptions():
    if Prescription.objects.exists():
        return
    rana = get_user_model().objects.get(username="rana")

    collected = demo_prescription("Samira", "Metformin", "500 mg", 60, 30, True)
    start_preparing(collected)
    ready_at = days_ago(28)
    collected_at = days_ago(27)
    log_id = latest_log_id()
    mark_ready(rana, collected)
    move_log_entries_after(log_id, ready_at)
    log_id = latest_log_id()
    mark_collected(rana, collected, "cash")
    move_log_entries_after(log_id, collected_at)
    Prescription.objects.filter(pk=collected.pk).update(
        created_at=days_ago(28),
        ready_at=ready_at,
        collected_at=collected_at,
    )
    Sale.objects.filter(patient=collected.patient, source="prescription").update(time=collected_at)

    waiting = demo_prescription("Omar's Father", "Atorvastatin", "20 mg", 30, 30, True)
    start_preparing(waiting)
    ready_at = days_ago(3)
    log_id = latest_log_id()
    mark_ready(rana, waiting)
    move_log_entries_after(log_id, ready_at)
    Prescription.objects.filter(pk=waiting.pk).update(created_at=days_ago(4), ready_at=ready_at)

    ready = demo_prescription("Maya", "Salbutamol inhaler", "100 mcg", 1, 30, False)
    start_preparing(ready)
    mark_ready(rana, ready)

    preparing = demo_prescription("Samira", "Amlodipine", "5 mg", 30, 30, True)
    start_preparing(preparing)

    demo_prescription("Ahmad", "Amoxicillin", "500 mg", 21, 7, False)


class Command(BaseCommand):
    def handle(self, *args, **options):
        create_staff()
        create_patients()
        create_stock()
        create_passports()
        create_prescriptions()
