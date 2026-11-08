from datetime import date, datetime, timezone
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from inventory.models import Batch, DrugClass, Medicine


def create_medicine(barcode="1000000000001"):
    penicillins, _ = DrugClass.objects.get_or_create(name="Penicillins")
    return Medicine.objects.create(
        name="Amoxicillin",
        strength="500 mg",
        barcode=barcode,
        drug_class=penicillins,
        price=Decimal("8.50"),
        minimum_quantity=20,
    )


def create_batch(medicine, **overrides):
    fields = {
        "medicine": medicine,
        "batch_number": "AMX-001",
        "expiry_date": date(2030, 1, 31),
        "quantity": 100,
        "unit_cost": Decimal("4.25"),
        "supplier": "Beirut Medical Supplies",
    }
    fields.update(overrides)
    return Batch.objects.create(**fields)


class DrugClassModelTests(TestCase):
    def test_t3_drug_class_is_saved_with_its_name(self):
        DrugClass.objects.create(name="Penicillins")
        self.assertTrue(DrugClass.objects.filter(name="Penicillins").exists())

    def test_t3_drug_class_name_is_unique(self):
        DrugClass.objects.create(name="Penicillins")
        with self.assertRaises(IntegrityError), transaction.atomic():
            DrugClass.objects.create(name="Penicillins")


class MedicineModelTests(TestCase):
    def test_t3_medicine_is_saved_with_all_its_fields(self):
        create_medicine()
        medicine = Medicine.objects.get(barcode="1000000000001")
        self.assertEqual(medicine.name, "Amoxicillin")
        self.assertEqual(medicine.strength, "500 mg")
        self.assertEqual(medicine.drug_class.name, "Penicillins")
        self.assertEqual(medicine.price, Decimal("8.50"))
        self.assertEqual(medicine.minimum_quantity, 20)

    def test_t3_barcode_is_unique(self):
        create_medicine(barcode="1000000000001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_medicine(barcode="1000000000001")

    def test_t3_drug_class_in_use_cannot_be_deleted(self):
        medicine = create_medicine()
        with self.assertRaises(ProtectedError):
            medicine.drug_class.delete()


class BatchModelTests(TestCase):
    def test_t3_batch_is_saved_with_all_its_fields(self):
        create_batch(create_medicine())
        batch = Batch.objects.get(batch_number="AMX-001")
        self.assertEqual(batch.medicine.name, "Amoxicillin")
        self.assertEqual(batch.expiry_date, date(2030, 1, 31))
        self.assertEqual(batch.quantity, 100)
        self.assertEqual(batch.unit_cost, Decimal("4.25"))
        self.assertEqual(batch.supplier, "Beirut Medical Supplies")

    def test_t3_medicine_can_have_several_batches(self):
        medicine = create_medicine()
        create_batch(medicine, batch_number="AMX-001")
        create_batch(medicine, batch_number="AMX-002")
        self.assertEqual(medicine.batch_set.count(), 2)

    def test_t3_batch_quantity_cannot_be_negative(self):
        medicine = create_medicine()
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_batch(medicine, quantity=-1)

    def test_t3_received_at_defaults_to_now(self):
        batch = create_batch(create_medicine())
        self.assertIsNotNone(batch.received_at)

    def test_t3_received_at_can_be_set_to_an_earlier_time(self):
        earlier_time = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        batch = create_batch(create_medicine(), received_at=earlier_time)
        batch.refresh_from_db()
        self.assertEqual(batch.received_at, earlier_time)

    def test_t3_medicine_with_batches_cannot_be_deleted(self):
        medicine = create_medicine()
        create_batch(medicine)
        with self.assertRaises(ProtectedError):
            medicine.delete()
