from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from inventory.models import Batch, DrugClass, Medicine



from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory.services import record_delivery, get_available_stock

from django.contrib.auth import get_user_model
from django.urls import reverse
import uuid


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



# Tests for SCRUM-29: Record a Delivery.
class DeliveryServiceTests(TestCase):

    # Runs before every test to prepare a medicine
    # and valid delivery information.
    def setUp(self):
        self.medicine = create_medicine()

        self.delivery_data = {
            "medicine": self.medicine,
            "supplier": "Beirut Medical Supplies",
            "batch_number": "NEW-001",
            "expiry_date": timezone.localdate() + timedelta(days=365),
            "quantity": 100,
            "unit_cost": Decimal("4.25"),
            "delivery_request_id": str(uuid.uuid4()),
        }

    # Test that a valid delivery creates a new batch.
    def test_record_delivery_creates_batch(self):
        batch = record_delivery(**self.delivery_data)

        self.assertEqual(batch.medicine, self.medicine)
        self.assertEqual(batch.quantity, 100)
        self.assertEqual(batch.batch_number, "NEW-001")
        self.assertEqual(Batch.objects.count(), 1)

    # Test that receiving a delivery increases available stock.
    def test_delivery_increases_stock(self):

        # Start with 8 units in stock.
        create_batch(self.medicine, quantity=8)

        # Receive another 100 units.
        record_delivery(**self.delivery_data)

        # Expected total: 8 + 100 = 108.
        self.assertEqual(
            get_available_stock(self.medicine), 108
        )

    # Test that expired batches are excluded from available stock.
    def test_expired_stock_is_excluded(self):
        batch = create_batch(self.medicine, quantity=50)

        # Change the batch expiry date to yesterday.
        batch.expiry_date = timezone.localdate() - timedelta(days=1)
        batch.save(update_fields=["expiry_date"])

        self.assertEqual(
            get_available_stock(self.medicine), 0
        )

    # Test that zero or negative quantities are rejected.
    def test_invalid_quantity_is_rejected(self):
        for quantity in [0, -5]:
            with self.subTest(quantity=quantity):
                data = self.delivery_data.copy()
                data["quantity"] = quantity

                with self.assertRaises(ValidationError):
                    record_delivery(**data)

        # Invalid deliveries must not create batches.
        self.assertEqual(Batch.objects.count(), 0)

    # Test that expired deliveries cannot be recorded.
    def test_expired_delivery_is_rejected(self):
        self.delivery_data["expiry_date"] = (
            timezone.localdate() - timedelta(days=1)
        )

        with self.assertRaises(ValidationError):
            record_delivery(**self.delivery_data)

        self.assertEqual(Batch.objects.count(), 0)

    # Test that a zero unit cost is rejected.
    def test_invalid_unit_cost_is_rejected(self):
        self.delivery_data["unit_cost"] = Decimal("0.00")

        with self.assertRaises(ValidationError):
            record_delivery(**self.delivery_data)

        self.assertEqual(Batch.objects.count(), 0)



# Tests for the Record Delivery web page (SCRUM-29).
class DeliveryViewTests(TestCase):


    # Repeating the same delivery request must not
    # create an additional batch or increase stock twice.
    def test_duplicate_request_creates_only_one_batch(self):

        self.client.force_login(self.pharmacist)

        # Submit the same delivery twice.
        first = self.client.post(
            self.url, self.delivery_data
        )

        second = self.client.post(
            self.url, self.delivery_data
        )

        # Both submissions should be handled successfully.
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)

        # Only one batch should exist.
        self.assertEqual(Batch.objects.count(), 1)

        # Stock must increase only once.
        self.assertEqual(
            get_available_stock(self.medicine), 10
        )


    # Prepare a medicine, a staff account, and the inventory URL.
    def setUp(self):
        self.medicine = create_medicine()

        # Create a pharmacist who is allowed to record deliveries.
        User = get_user_model()
        self.pharmacist = User.objects.create_user(
            username="test_pharmacist",
            password="testpassword123",
            role="pharmacist",
        )

        # Get the inventory page URL from Django.
        self.url = reverse("inventory:overview")


        # Valid information used to test a delivery submission.
        self.delivery_data = {
            "medicine": self.medicine.pk,
            "supplier": "Beirut Medical Supplies",
            "batch_number": "TEST-VIEW-001",
            "expiry_date": (
                timezone.localdate() + timedelta(days=365)
            ).isoformat(),
            "quantity": 10,
            "unit_cost": "2.50",

            # Unique ID used to identify this delivery request.
            "delivery_request_id": str(uuid.uuid4()),
        }


    # Users must sign in before accessing the inventory page.
    def test_anonymous_user_is_redirected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    # Patients must not be allowed to access or change inventory.
    def test_patient_cannot_access_inventory(self):
        User = get_user_model()

        patient = User.objects.create_user(
            username="test_patient",
            password="testpassword123",
            role="patient",
        )

        self.client.force_login(patient)

        # Attempt to view the inventory page.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

        # Attempt to submit a delivery directly.
        response = self.client.post(
            self.url,
            self.delivery_data
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Batch.objects.count(), 0)

    # Pharmacists should be able to open the inventory page.
    def test_pharmacist_can_view_inventory(self):
        self.client.force_login(self.pharmacist)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    # A valid form submission must save the delivery.
    def test_valid_delivery_is_saved(self):
        self.client.force_login(self.pharmacist)

        response = self.client.post(
            self.url,
            self.delivery_data
        )

        # Successful submissions redirect to prevent refresh duplicates.
        self.assertRedirects(response, self.url)

        # Confirm the batch was saved with the correct quantity.
        batch = Batch.objects.get(
            batch_number="TEST-VIEW-001"
        )

        self.assertEqual(batch.quantity, 10)
        self.assertEqual(batch.medicine, self.medicine)

    # Invalid quantities must not create database records.
    def test_invalid_delivery_is_rejected(self):
        self.client.force_login(self.pharmacist)

        # Replace the valid quantity with zero.
        invalid_data = self.delivery_data.copy()
        invalid_data["quantity"] = 0

        response = self.client.post(
            self.url,
            invalid_data
        )

        # The page should redisplay the form with errors.
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "quantity",
            response.context["form"].errors
        )

        # Confirm no delivery was recorded.
        self.assertEqual(Batch.objects.count(), 0)



# SCRUM-33: Test the near-expiry inventory functionality.
class NearExpiryViewTests(TestCase):

    def setUp(self):
        # Create a medicine and a pharmacist.
        self.medicine = create_medicine()

        User = get_user_model()
        self.pharmacist = User.objects.create_user(
            username="expiry_pharmacist",
            password="testpassword123",
            role="pharmacist",
        )

        self.url = reverse("inventory:overview")

    def test_near_expiry_batches(self):
        today = timezone.localdate()

        # Helper: Create a batch with a relative expiry date.
        def add_batch(number, days, quantity=10):
            batch = create_batch(
                self.medicine,
                batch_number=number,
                quantity=quantity,
            )

            batch.expiry_date = today + timedelta(days=days)
            batch.save(update_fields=["expiry_date"])

        # Different situations to test.
        add_batch("SOON-001", 1)
        add_batch("DAY-090", 90)
        add_batch("DAY-091", 91)
        add_batch("EXPIRED", -1)
        add_batch("EMPTY", 30, quantity=0)

        # Open the inventory dashboard.
        self.client.force_login(self.pharmacist)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        # Only two batches should be considered near expiry.
        self.assertEqual(
            response.context["expiring_count"], 2
        )

        # Verify the status assigned to each batch.
        statuses = {
            row["batch"].batch_number: row["status"]
            for row in response.context["inventory_rows"]
            if row["batch"] is not None
        }

        self.assertEqual(statuses["SOON-001"], "Expiring")
        self.assertEqual(statuses["DAY-090"], "Expiring")
        self.assertEqual(statuses["DAY-091"], "In stock")
        self.assertEqual(statuses["EXPIRED"], "Expired")

        # An empty batch should not be labeled Expiring.
        self.assertNotEqual(statuses["EMPTY"], "Expiring")