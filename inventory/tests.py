from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from inventory.models import Batch, DrugClass, Medicine


def create_medicine(barcode="1000000000001"):
    penicillins, created = DrugClass.objects.get_or_create(name="Penicillins")
    return Medicine.objects.create(
        name="Amoxicillin",
        strength="500 mg",
        barcode=barcode,
        drug_class=penicillins,
        price=Decimal("8.50"),
        minimum_quantity=20,
    )


def create_batch(medicine, batch_number="AMX-001", quantity=100):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=date(2030, 1, 31),
        quantity=quantity,
        unit_cost=Decimal("4.25"),
        supplier="Beirut Medical Supplies",
    )


class DrugClassModelTests(TestCase):
    def test_t3_drug_class_is_saved_with_its_name(self):
        DrugClass.objects.create(name="Penicillins")
        self.assertTrue(DrugClass.objects.filter(name="Penicillins").exists())

    def test_t3_drug_class_name_is_unique(self):
        DrugClass.objects.create(name="Penicillins")
        with self.assertRaises(IntegrityError):
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
        with self.assertRaises(IntegrityError):
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
        with self.assertRaises(IntegrityError):
            create_batch(medicine, quantity=-1)

    def test_t3_received_at_is_set_automatically(self):
        batch = create_batch(create_medicine())
        self.assertIsNotNone(batch.received_at)

    def test_t3_medicine_with_batches_cannot_be_deleted(self):
        medicine = create_medicine()
        create_batch(medicine)
        with self.assertRaises(ProtectedError):
            medicine.delete()


class MedicineAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser(username="nadim", password="pass", role="owner")
        cls.pharmacist = User.objects.create_user(username="rana", password="pass", role="pharmacist")
        cls.medicine = create_medicine()

    def test_t8_the_owner_can_open_the_medicines(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/admin/inventory/medicine/"), "Amoxicillin 500 mg")

    def test_t8_the_owner_can_open_the_drug_classes(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/admin/inventory/drugclass/"), "Penicillins")

    def test_t8_the_owner_can_change_a_price(self):
        self.client.force_login(self.owner)
        self.client.post(
            f"/admin/inventory/medicine/{self.medicine.pk}/change/",
            {
                "name": "Amoxicillin",
                "strength": "500 mg",
                "barcode": "1000000000001",
                "drug_class": self.medicine.drug_class.pk,
                "price": "9.75",
                "minimum_quantity": 20,
            },
        )
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.price, Decimal("9.75"))

    def test_t8_the_owner_can_add_a_drug_class(self):
        self.client.force_login(self.owner)
        self.client.post("/admin/inventory/drugclass/add/", {"name": "Macrolides"})
        self.assertTrue(DrugClass.objects.filter(name="Macrolides").exists())

    def test_t8_a_pharmacist_cannot_open_the_medicines(self):
        self.client.force_login(self.pharmacist)
        response = self.client.get("/admin/inventory/medicine/")
        self.assertRedirects(
            response,
            "/admin/login/?next=/admin/inventory/medicine/",
            fetch_redirect_response=False,
        )

    def test_t8_batches_are_not_in_the_admin(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/admin/inventory/batch/").status_code, 404)
