
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.models import DrugClass, Medicine
from reports.services import (
    payment_breakdown,
    sales_summary,
    top_selling_medicines,
)
from sales.models import Sale
from sales.services import record_sale


# SCRUM-42: Automated tests for Sales Reports.
class SalesReportTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        cls.owner = User.objects.create_user(
            username="report_owner", role="owner"
        )
        cls.pharmacist = User.objects.create_user(
            username="report_pharmacist", role="pharmacist"
        )
        cls.assistant = User.objects.create_user(
            username="report_assistant", role="assistant"
        )
        cls.patient = User.objects.create_user(
            username="report_patient", role="patient"
        )

        drug_class = DrugClass.objects.create(
            name="Report test medicines"
        )

        cls.panadol = Medicine.objects.create(
            name="Panadol",
            strength="500 mg",
            barcode="REPORT001",
            drug_class=drug_class,
            price=Decimal("5.00"),
            minimum_quantity=10,
        )

        cls.vitamin_c = Medicine.objects.create(
            name="Vitamin C",
            strength="1000 mg",
            barcode="REPORT002",
            drug_class=drug_class,
            price=Decimal("8.00"),
            minimum_quantity=10,
        )

        # Cash sale: $18
        cash_sale = record_sale(
            cls.owner,
            "cash",
            [(cls.panadol, 2), (cls.vitamin_c, 1)],
            "counter",
        )

        # Card sale: $29
        card_sale = record_sale(
            cls.owner,
            "card",
            [(cls.panadol, 1), (cls.vitamin_c, 3)],
            "counter",
        )

        Sale.objects.filter(pk=cash_sale.pk).update(
            time=timezone.make_aware(
                datetime(2026, 11, 9, 12, 0)
            )
        )

        Sale.objects.filter(pk=card_sale.pk).update(
            time=timezone.make_aware(
                datetime(2026, 11, 10, 12, 0)
            )
        )

    def test_total_sales_summary(self):
        result = sales_summary()

        self.assertEqual(result["sales_count"], 2)
        self.assertEqual(result["items_sold"], 7)
        self.assertEqual(
            result["total_revenue"], Decimal("47.00")
        )

    def test_payment_breakdown(self):
        result = payment_breakdown()

        self.assertEqual(
            result["cash_revenue"], Decimal("18.00")
        )
        self.assertEqual(
            result["card_revenue"], Decimal("29.00")
        )

    def test_best_selling_medicines(self):
        result = top_selling_medicines()

        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0]["medicine__name"], "Vitamin C"
        )
        self.assertEqual(result[0]["quantity_sold"], 4)
        self.assertEqual(
            result[0]["revenue"], Decimal("32.00")
        )

    def test_date_filter(self):
        result = sales_summary(
            date(2026, 11, 10),
            date(2026, 11, 10),
        )

        self.assertEqual(result["sales_count"], 1)
        self.assertEqual(result["items_sold"], 4)
        self.assertEqual(
            result["total_revenue"], Decimal("29.00")
        )

    def test_historical_prices_are_preserved(self):
        self.panadol.price = Decimal("20.00")
        self.panadol.save()

        result = sales_summary()

        self.assertEqual(
            result["total_revenue"], Decimal("47.00")
        )

    def test_owner_can_view_reports(self):
        self.client.force_login(self.owner)

        response = self.client.get("/reports/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales Reports")
        self.assertContains(response, "47.00")
        self.assertContains(response, "Vitamin C")

    def test_other_roles_cannot_view_reports(self):
        for user in [
            self.pharmacist,
            self.assistant,
            self.patient,
        ]:
            self.client.force_login(user)

            response = self.client.get("/reports/")
            self.assertEqual(response.status_code, 403)

    def test_invalid_date_range_is_rejected(self):
        self.client.force_login(self.owner)

        response = self.client.get(
            "/reports/",
            {
                "start_date": "2026-11-30",
                "end_date": "2026-11-01",
            },
        )

        self.assertContains(
            response,
            "Start date cannot be after end date.",
        )

    def test_no_sales_in_selected_period(self):
        result = sales_summary(
            date(2035, 1, 1),
            date(2035, 1, 31),
        )

        self.assertEqual(result["sales_count"], 0)
        self.assertEqual(result["items_sold"], 0)
        self.assertEqual(
            result["total_revenue"], Decimal("0.00")
        )
