from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from sales.forms import PaymentForm
from sales.models import Sale, SaleItem
from sales.services import (
    add_one,
    complete_sale,
    medicine_cards,
    record_sale,
    remove_one,
    sale_lines,
    sale_total,
)


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


def make_batch(medicine, batch_number, quantity=100, days_until_expiry=300):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=timezone.localdate() + timedelta(days=days_until_expiry),
        quantity=quantity,
        unit_cost=Decimal("4.25"),
        supplier="Cedar Medical Supplies",
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


class CurrentSaleTests(SaleTestData):
    def setUp(self):
        make_batch(self.amoxicillin, "AMX-001", quantity=30)
        self.current_sale = {}

    def test_5_1a_adding_a_medicine_puts_one_in_the_sale(self):
        add_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {str(self.amoxicillin.pk): 1})

    def test_5_1a_adding_it_again_raises_the_quantity(self):
        add_one(self.current_sale, self.amoxicillin)
        add_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {str(self.amoxicillin.pk): 2})

    def test_5_1a_removing_one_lowers_the_quantity(self):
        add_one(self.current_sale, self.amoxicillin)
        add_one(self.current_sale, self.amoxicillin)
        remove_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {str(self.amoxicillin.pk): 1})

    def test_5_1a_removing_the_last_one_takes_the_line_away(self):
        add_one(self.current_sale, self.amoxicillin)
        remove_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {})

    def test_5_1a_removing_a_medicine_not_in_the_sale_changes_nothing(self):
        remove_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {})

    def test_5_1a_adding_beyond_the_available_stock_is_refused(self):
        self.current_sale[str(self.amoxicillin.pk)] = 30
        with self.assertRaisesMessage(ValidationError, "Not enough Amoxicillin 500 mg in stock: 30 available."):
            add_one(self.current_sale, self.amoxicillin)
        self.assertEqual(self.current_sale, {str(self.amoxicillin.pk): 30})

    def test_5_1a_a_medicine_with_no_stock_cannot_be_added(self):
        with self.assertRaisesMessage(ValidationError, "Not enough Metformin 500 mg in stock: 0 available."):
            add_one(self.current_sale, self.metformin)
        self.assertEqual(self.current_sale, {})

    def test_5_1a_each_line_shows_its_price_and_the_stock_after_the_sale(self):
        add_one(self.current_sale, self.amoxicillin)
        add_one(self.current_sale, self.amoxicillin)
        line = sale_lines(self.current_sale)[0]
        self.assertEqual(line["medicine"], self.amoxicillin)
        self.assertEqual(line["quantity"], 2)
        self.assertEqual(line["line_total"], Decimal("17.00"))
        self.assertEqual(line["available"], 30)
        self.assertEqual(line["available_after"], 28)

    def test_5_1a_lines_keep_the_order_they_were_added_in(self):
        make_batch(self.metformin, "MET-001")
        add_one(self.current_sale, self.metformin)
        add_one(self.current_sale, self.amoxicillin)
        lines = sale_lines(self.current_sale)
        self.assertEqual([line["medicine"] for line in lines], [self.metformin, self.amoxicillin])

    def test_5_1a_the_total_adds_up_every_line(self):
        make_batch(self.metformin, "MET-001")
        add_one(self.current_sale, self.amoxicillin)
        add_one(self.current_sale, self.amoxicillin)
        for count in range(3):
            add_one(self.current_sale, self.metformin)
        self.assertEqual(sale_total(sale_lines(self.current_sale)), Decimal("35.00"))


class CompleteSaleTests(SaleTestData):
    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity

    def test_5_1a_completing_a_sale_takes_the_stock(self):
        batch = make_batch(self.amoxicillin, "AMX-001", quantity=30)
        complete_sale(self.karim, "cash", {str(self.amoxicillin.pk): 2})
        self.assertEqual(self.quantity_of(batch), 28)

    def test_5_1a_each_line_uses_the_batch_that_expires_first(self):
        later = make_batch(self.amoxicillin, "LATER", quantity=100, days_until_expiry=300)
        sooner = make_batch(self.amoxicillin, "SOONER", quantity=100, days_until_expiry=30)
        complete_sale(self.karim, "cash", {str(self.amoxicillin.pk): 1})
        self.assertEqual(self.quantity_of(sooner), 99)
        self.assertEqual(self.quantity_of(later), 100)

    def test_5_1a_the_sale_is_recorded_from_the_counter_at_today_s_prices(self):
        make_batch(self.amoxicillin, "AMX-001")
        sale = complete_sale(self.karim, "card", {str(self.amoxicillin.pk): 2})
        self.assertEqual(sale.staff, self.karim)
        self.assertEqual(sale.payment_method, "card")
        self.assertEqual(sale.source, "counter")
        self.assertIsNone(sale.patient)
        line = sale.saleitem_set.get()
        self.assertEqual((line.medicine, line.quantity, line.unit_price), (self.amoxicillin, 2, Decimal("8.50")))

    def test_5_1a_a_sale_of_two_medicines_has_two_lines(self):
        make_batch(self.amoxicillin, "AMX-001")
        make_batch(self.metformin, "MET-001")
        sale = complete_sale(self.karim, "cash", {str(self.amoxicillin.pk): 2, str(self.metformin.pk): 3})
        self.assertEqual(sale.saleitem_set.count(), 2)

    def test_5_1a_the_stock_change_and_the_sale_are_logged(self):
        make_batch(self.amoxicillin, "AMX-001")
        complete_sale(self.karim, "cash", {str(self.amoxicillin.pk): 2})
        entries = ActivityLog.objects.order_by("id")
        self.assertEqual([entry.action for entry in entries], ["Took stock", "Recorded sale"])
        self.assertEqual({entry.user for entry in entries}, {self.karim})

    def test_5_1a_an_empty_sale_cannot_be_completed(self):
        with self.assertRaisesMessage(ValidationError, "Add at least one medicine first."):
            complete_sale(self.karim, "cash", {})
        self.assertFalse(Sale.objects.exists())

    def test_5_2_a_sale_can_be_linked_to_a_patient(self):
        make_batch(self.amoxicillin, "AMX-001")
        sale = complete_sale(self.karim, "cash", {str(self.amoxicillin.pk): 2}, patient=self.samira)
        self.assertEqual(sale.patient, self.samira)
        self.assertEqual(sale.source, "counter")

    def test_5_1a_a_line_no_single_batch_can_fill_changes_nothing(self):
        amoxicillin_batch = make_batch(self.amoxicillin, "AMX-001", quantity=30)
        make_batch(self.metformin, "MET-A", quantity=5)
        make_batch(self.metformin, "MET-B", quantity=5)
        current_sale = {str(self.amoxicillin.pk): 2, str(self.metformin.pk): 8}
        with self.assertRaisesMessage(ValidationError, "No single batch of Metformin 500 mg has 8."):
            complete_sale(self.karim, "cash", current_sale)
        self.assertEqual(self.quantity_of(amoxicillin_batch), 30)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(ActivityLog.objects.exists())


class MedicineCardsTests(SaleTestData):
    def test_5_1a_every_medicine_has_a_card_with_its_available_stock(self):
        make_batch(self.amoxicillin, "AMX-001", quantity=30)
        cards = {card["medicine"]: card["available"] for card in medicine_cards()}
        self.assertEqual(cards, {self.amoxicillin: 30, self.metformin: 0})

    def test_5_1a_a_medicine_below_its_minimum_is_marked_low(self):
        make_batch(self.amoxicillin, "AMX-001", quantity=30)
        make_batch(self.metformin, "MET-001", quantity=5)
        cards = {card["medicine"]: card["low"] for card in medicine_cards()}
        self.assertEqual(cards, {self.amoxicillin: False, self.metformin: True})

    def test_5_1a_the_search_finds_medicines_by_name(self):
        self.assertEqual([card["medicine"] for card in medicine_cards("metf")], [self.metformin])


class PaymentFormTests(TestCase):
    def test_5_1a_payment_is_cash_or_card(self):
        choices = PaymentForm().fields["payment_method"].choices
        self.assertEqual([value for value, label in choices], ["cash", "card"])

    def test_5_1a_a_payment_method_is_required(self):
        self.assertFalse(PaymentForm({}).is_valid())


class SalePagesTestData(SaleTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.nadim = User.objects.create(username="nadim", role="owner", is_pharmacist=True)
        cls.rana = User.objects.create(username="rana", role="pharmacist")
        cls.maya = User.objects.create(username="maya", role="patient")


class SalePageAccessTests(SalePagesTestData):
    def test_5_1a_signed_out_visitors_go_to_the_staff_login(self):
        self.assertRedirects(self.client.get("/sales/"), "/login/staff/?next=/sales/")

    def test_5_1a_patient_side_users_are_refused(self):
        self.client.force_login(self.maya)
        self.assertEqual(self.client.get("/sales/").status_code, 403)
        self.assertEqual(self.client.post(f"/sales/add/{self.amoxicillin.pk}/").status_code, 403)
        self.assertEqual(self.client.post("/sales/complete/", {"payment_method": "cash"}).status_code, 403)

    def test_5_1a_every_staff_role_can_sell(self):
        for user in [self.nadim, self.rana, self.karim]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/sales/").status_code, 200)

    def test_5_1a_the_menu_links_to_the_sales_page(self):
        self.client.force_login(self.karim)
        self.assertContains(self.client.get("/sales/"), 'href="/sales/"')


class SalePageTests(SalePagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)
        self.batch = make_batch(self.amoxicillin, "AMX-001", quantity=30)

    def add(self, medicine, follow=False):
        return self.client.post(f"/sales/add/{medicine.pk}/", follow=follow)

    def remove(self, medicine):
        return self.client.post(f"/sales/remove/{medicine.pk}/")

    def complete(self, payment_method="cash"):
        return self.client.post("/sales/complete/", {"payment_method": payment_method}, follow=True)

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity

    def test_5_1a_page_uses_the_figma_wording(self):
        response = self.client.get("/sales/")
        self.assertContains(response, "Pharmacist console")
        self.assertContains(
            response,
            "Scan or search a medicine. Stock goes down on its own when the sale is completed.",
        )
        self.assertContains(response, 'placeholder="Search a medicine or scan its barcode"')
        self.assertContains(response, "Current Sale")

    def test_5_1a_each_medicine_has_a_card_with_its_price_and_stock(self):
        response = self.client.get("/sales/")
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, "$8.50")
        self.assertContains(response, "30 in stock")

    def test_5_1a_a_low_medicine_says_how_many_are_left(self):
        make_batch(self.metformin, "MET-001", quantity=5)
        self.assertContains(self.client.get("/sales/"), "5 left")

    def test_5_1a_an_out_of_stock_medicine_cannot_be_added(self):
        response = self.client.get("/sales/")
        self.assertContains(response, "Out of stock")
        self.assertContains(response, f'action="/sales/add/{self.amoxicillin.pk}/"')
        self.assertNotContains(response, f'action="/sales/add/{self.metformin.pk}/"')

    def test_5_1a_the_search_shows_only_matching_medicines(self):
        response = self.client.get("/sales/", {"search": "metf"})
        self.assertContains(response, "Metformin 500 mg")
        self.assertNotContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, 'value="metf"')

    def test_5_1a_an_empty_sale_says_so(self):
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")

    def test_5_1a_adding_a_medicine_puts_it_in_the_current_sale(self):
        self.assertRedirects(self.add(self.amoxicillin), "/sales/")
        response = self.client.get("/sales/")
        self.assertContains(response, "Stock after sale: 30 → 29")
        self.assertContains(response, "Complete Sale · $8.50")
        self.assertEqual(self.quantity_of(self.batch), 30)

    def test_5_1a_the_plus_and_minus_buttons_change_the_quantity(self):
        self.add(self.amoxicillin)
        self.add(self.amoxicillin)
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 28")
        self.assertRedirects(self.remove(self.amoxicillin), "/sales/")
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 29")

    def test_5_1a_removing_the_last_one_empties_the_sale(self):
        self.add(self.amoxicillin)
        self.remove(self.amoxicillin)
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")

    def test_5_1a_two_medicines_are_counted_together(self):
        make_batch(self.metformin, "MET-001")
        self.add(self.amoxicillin)
        self.add(self.metformin)
        response = self.client.get("/sales/")
        self.assertContains(response, "Complete Sale · $14.50")
        self.assertContains(response, "Stock updates automatically for all 2 items.")

    def test_5_1a_adding_more_than_the_stock_is_refused(self):
        make_batch(self.metformin, "MET-001", quantity=1)
        self.add(self.metformin)
        response = self.add(self.metformin, follow=True)
        self.assertContains(response, "Not enough Metformin 500 mg in stock: 1 available.")
        self.assertContains(response, "Stock after sale: 1 → 0")

    def test_5_1a_opening_a_button_address_changes_nothing(self):
        self.client.get(f"/sales/add/{self.amoxicillin.pk}/")
        self.client.get("/sales/complete/")
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")
        self.assertFalse(Sale.objects.exists())

    def test_5_1a_an_unknown_medicine_is_not_found(self):
        self.assertEqual(self.client.post("/sales/add/9999/").status_code, 404)

    def test_5_1a_completing_the_sale_takes_the_stock_and_records_it(self):
        self.add(self.amoxicillin)
        self.add(self.amoxicillin)
        self.complete("cash")
        self.assertEqual(self.quantity_of(self.batch), 28)
        sale = Sale.objects.get()
        self.assertEqual((sale.staff, sale.payment_method, sale.source), (self.karim, "cash", "counter"))

    def test_5_1a_a_completed_sale_is_confirmed_and_the_sale_is_emptied(self):
        self.add(self.amoxicillin)
        self.add(self.amoxicillin)
        response = self.complete("card")
        sale = Sale.objects.get()
        self.assertRedirects(response, "/sales/")
        self.assertContains(response, f"Sale {sale.pk} completed: $17.00, paid by card.")
        self.assertContains(response, "No medicines in this sale yet.")

    def test_5_1a_a_payment_method_is_required(self):
        self.add(self.amoxicillin)
        response = self.complete("")
        self.assertContains(response, "Choose cash or card.")
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(self.quantity_of(self.batch), 30)

    def test_5_1a_an_empty_sale_cannot_be_completed(self):
        response = self.complete("cash")
        self.assertContains(response, "Add at least one medicine first.")
        self.assertFalse(Sale.objects.exists())

    def test_5_1a_a_refused_sale_keeps_the_current_sale_and_changes_nothing(self):
        make_batch(self.metformin, "MET-A", quantity=5)
        make_batch(self.metformin, "MET-B", quantity=5)
        self.add(self.amoxicillin)
        for count in range(8):
            self.add(self.metformin)
        response = self.complete("cash")
        self.assertContains(response, "No single batch of Metformin 500 mg has 8.")
        self.assertContains(response, "Stock after sale: 10 → 2")
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(self.quantity_of(self.batch), 30)


class ScanBarcodeTests(SalePagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)
        self.batch = make_batch(self.amoxicillin, "AMX-001", quantity=30)

    def scan(self, code, follow=False):
        return self.client.post("/sales/scan/", {"search": code}, follow=follow)

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity

    def test_5_1b_the_search_box_sends_to_the_scan_address(self):
        response = self.client.get("/sales/")
        self.assertContains(response, 'action="/sales/scan/"')
        self.assertContains(response, "autofocus")
        self.assertContains(response, "sale-barcode.svg")

    def test_5_1b_scanning_a_barcode_adds_the_medicine(self):
        self.assertRedirects(self.scan("1000000000001"), "/sales/")
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 29")

    def test_5_1b_a_scan_is_confirmed(self):
        response = self.scan("1000000000001", follow=True)
        self.assertContains(response, "Amoxicillin 500 mg added to the sale.")

    def test_5_1b_scanning_again_adds_one_more(self):
        self.scan("1000000000001")
        self.scan("1000000000001")
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 28")

    def test_5_1b_spaces_around_the_code_are_ignored(self):
        self.scan(" 1000000000001 ")
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 29")

    def test_5_1b_scanning_does_not_take_stock_yet(self):
        self.scan("1000000000001")
        self.assertEqual(self.quantity_of(self.batch), 30)
        self.assertFalse(Sale.objects.exists())

    def test_5_1b_a_scanned_medicine_with_no_stock_is_refused(self):
        response = self.scan("1000000000004", follow=True)
        self.assertContains(response, "Not enough Metformin 500 mg in stock: 0 available.")
        self.assertContains(response, "No medicines in this sale yet.")

    def test_5_1b_a_name_is_searched_instead(self):
        self.assertRedirects(self.scan("metf"), "/sales/?search=metf")
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")

    def test_5_1b_part_of_a_barcode_is_searched_not_added(self):
        self.assertRedirects(self.scan("100000000000"), "/sales/?search=100000000000")
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")

    def test_5_1b_an_empty_box_shows_every_medicine(self):
        self.assertRedirects(self.scan(""), "/sales/")

    def test_5_1b_opening_the_scan_address_changes_nothing(self):
        self.assertRedirects(self.client.get("/sales/scan/", {"search": "1000000000001"}), "/sales/")
        self.assertContains(self.client.get("/sales/"), "No medicines in this sale yet.")

    def test_5_1b_patient_side_users_cannot_scan(self):
        self.client.force_login(self.maya)
        self.assertEqual(self.scan("1000000000001").status_code, 403)


class LinkPatientTests(SalePagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)
        self.batch = make_batch(self.amoxicillin, "AMX-001", quantity=30)

    def link(self, patient_id, follow=False):
        return self.client.post("/sales/patient/", {"patient": patient_id}, follow=follow)

    def add(self, medicine):
        return self.client.post(f"/sales/add/{medicine.pk}/")

    def complete(self, payment_method="cash"):
        return self.client.post("/sales/complete/", {"payment_method": payment_method}, follow=True)

    def test_5_2_the_customer_box_starts_as_a_walk_in(self):
        response = self.client.get("/sales/")
        self.assertContains(response, "Customer: Walk-in")
        self.assertContains(response, "Link a patient")
        self.assertContains(response, 'href="/sales/patient/"')

    def test_5_2_link_a_patient_opens_a_popup_over_the_sale(self):
        response = self.client.get("/sales/patient/")
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "Link a Patient")
        self.assertContains(response, 'name="patient"')
        self.assertContains(response, "Samira")
        self.assertContains(response, "Link Patient")
        self.assertContains(response, "Current Sale")
        self.assertContains(response, 'href="/sales/"')

    def test_5_2_linking_a_patient_shows_them_in_the_box(self):
        self.assertRedirects(self.link(self.samira.pk), "/sales/")
        response = self.client.get("/sales/")
        self.assertContains(response, "Customer: Samira")
        self.assertContains(response, 'action="/sales/patient/remove/"')
        self.assertNotContains(response, "Customer: Walk-in")

    def test_5_2_linking_is_confirmed(self):
        self.assertContains(self.link(self.samira.pk, follow=True), "Samira linked to this sale.")

    def test_5_2_a_patient_must_be_chosen(self):
        response = self.link("")
        self.assertContains(response, "<dialog open")
        self.assertContains(self.client.get("/sales/"), "Customer: Walk-in")

    def test_5_2_linking_keeps_the_medicines_in_the_sale(self):
        self.add(self.amoxicillin)
        self.link(self.samira.pk)
        self.assertContains(self.client.get("/sales/"), "Stock after sale: 30 → 29")

    def test_5_2_removing_the_patient_goes_back_to_a_walk_in(self):
        self.link(self.samira.pk)
        self.assertRedirects(self.client.post("/sales/patient/remove/"), "/sales/")
        self.assertContains(self.client.get("/sales/"), "Customer: Walk-in")

    def test_5_2_opening_the_remove_address_changes_nothing(self):
        self.link(self.samira.pk)
        self.client.get("/sales/patient/remove/")
        self.assertContains(self.client.get("/sales/"), "Customer: Samira")

    def test_5_2_completing_saves_the_patient_on_the_sale(self):
        self.add(self.amoxicillin)
        self.link(self.samira.pk)
        response = self.complete("cash")
        sale = Sale.objects.get()
        self.assertEqual(sale.patient, self.samira)
        self.assertContains(response, f"Sale {sale.pk} completed: $8.50, paid by cash, linked to Samira.")

    def test_5_2_the_next_customer_starts_as_a_walk_in(self):
        self.add(self.amoxicillin)
        self.link(self.samira.pk)
        self.assertContains(self.complete("cash"), "Customer: Walk-in")

    def test_5_2_a_sale_with_no_patient_stays_a_walk_in_sale(self):
        self.add(self.amoxicillin)
        self.complete("cash")
        self.assertIsNone(Sale.objects.get().patient)

    def test_5_2_a_refused_sale_keeps_its_patient(self):
        self.link(self.samira.pk)
        response = self.complete("cash")
        self.assertContains(response, "Add at least one medicine first.")
        self.assertContains(response, "Customer: Samira")

    def test_5_2_signed_out_visitors_go_to_the_staff_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/sales/patient/"), "/login/staff/?next=/sales/patient/")

    def test_5_2_patient_side_users_cannot_link(self):
        self.client.force_login(self.maya)
        self.assertEqual(self.client.get("/sales/patient/").status_code, 403)
        self.assertEqual(self.link(self.samira.pk).status_code, 403)
        self.assertEqual(self.client.post("/sales/patient/remove/").status_code, 403)
