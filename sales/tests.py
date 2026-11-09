from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from sales.models import Sale, SaleItem
from sales.services import record_sale


def make_medicine(name, barcode, price):
    drug_class, created = DrugClass.objects.get_or_create(name="Test class")
    return Medicine.objects.create(
        name=name,
        strength="500 mg",
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal(price),
        minimum_quantity=10,
    )


class SaleTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.karim = get_user_model().objects.create(username="karim", role="assistant")
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.amoxicillin = make_medicine("Amoxicillin", "1000000000001", "8.50")
        cls.metformin = make_medicine("Metformin", "1000000000004", "6.00")


class SaleModelTests(SaleTestData):
    def test_1_1_payment_is_cash_or_card(self):
        values = [value for value, label in Sale.PAYMENT_CHOICES]
        self.assertEqual(values, ["cash", "card"])

    def test_1_1_a_sale_comes_from_the_counter_a_prescription_or_a_reservation(self):
        values = [value for value, label in Sale.SOURCE_CHOICES]
        self.assertEqual(values, ["counter", "prescription", "reservation"])

    def test_1_1_a_sold_medicine_cannot_be_deleted(self):
        record_sale(self.karim, "cash", [(self.amoxicillin, 2)], "counter")
        with self.assertRaises(ProtectedError):
            self.amoxicillin.delete()


class RecordSaleTests(SaleTestData):
    def test_1_1_a_sale_saves_who_how_and_where_it_came_from(self):
        sale = record_sale(self.karim, "cash", [(self.amoxicillin, 2)], "counter")
        self.assertEqual(sale.staff, self.karim)
        self.assertEqual(sale.payment_method, "cash")
        self.assertEqual(sale.source, "counter")
        self.assertIsNone(sale.patient)
        self.assertIsNotNone(sale.time)

    def test_1_1_a_sale_can_be_linked_to_a_patient(self):
        sale = record_sale(self.karim, "card", [(self.amoxicillin, 2)], "prescription", self.samira)
        self.assertEqual(sale.patient, self.samira)

    def test_1_1_a_sale_has_one_line_per_medicine(self):
        sale = record_sale(self.karim, "cash", [(self.amoxicillin, 2), (self.metformin, 60)], "counter")
        lines = sale.saleitem_set.order_by("id")
        self.assertEqual(
            [(line.medicine, line.quantity) for line in lines],
            [(self.amoxicillin, 2), (self.metformin, 60)],
        )

    def test_1_1_each_line_keeps_the_price_at_the_time_of_sale(self):
        record_sale(self.karim, "cash", [(self.amoxicillin, 2)], "counter")
        self.amoxicillin.price = Decimal("9.75")
        self.amoxicillin.save()
        self.assertEqual(SaleItem.objects.get().unit_price, Decimal("8.50"))

    def test_1_1_recording_a_sale_does_not_touch_stock(self):
        batch = Batch.objects.create(
            medicine=self.amoxicillin,
            batch_number="AMX-001",
            expiry_date=timezone.localdate() + timedelta(days=300),
            quantity=100,
            unit_cost=Decimal("4.25"),
            supplier="Cedar Medical Supplies",
        )
        record_sale(self.karim, "cash", [(self.amoxicillin, 2)], "counter")
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 100)

    def test_1_1_a_sale_is_logged_with_who_recorded_it(self):
        sale = record_sale(self.karim, "card", [(self.amoxicillin, 2)], "prescription", self.samira)
        entry = ActivityLog.objects.get()
        self.assertEqual(entry.user, self.karim)
        self.assertEqual(entry.action, "Recorded sale")
        self.assertEqual(entry.details, f"Sale {sale.pk} · Prescription · Card")

    def test_1_1_a_sale_that_cannot_be_logged_saves_nothing(self):
        with patch("sales.services.log_action", side_effect=ValueError):
            with self.assertRaises(ValueError):
                record_sale(self.karim, "cash", [(self.amoxicillin, 2)], "counter")
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(SaleItem.objects.exists())
