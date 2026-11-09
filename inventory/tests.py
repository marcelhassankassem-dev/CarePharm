from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from inventory.services import (
    available_stock,
    deliveries_today_count,
    medicine_count,
    medicines_in_stock_count,
    record_delivery,
    stock_rows,
)


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


def create_aspirin():
    nsaids, created = DrugClass.objects.get_or_create(name="NSAIDs")
    return Medicine.objects.create(
        name="Aspirin",
        strength="81 mg",
        barcode="2000000000001",
        drug_class=nsaids,
        price=Decimal("3.50"),
        minimum_quantity=30,
    )


def create_batch(medicine, batch_number="AMX-001", quantity=100, expiry_date=date(2030, 1, 31)):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=expiry_date,
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


class DeliveryServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.karim = get_user_model().objects.create_user(
            username="karim",
            password="pass",
            role="assistant",
        )
        cls.medicine = create_medicine()

    def deliver(self, batch_number="NEW-001", quantity=100):
        return record_delivery(
            self.karim,
            self.medicine,
            "Beirut Medical Supplies",
            batch_number,
            date(2030, 6, 30),
            quantity,
            Decimal("4.25"),
        )

    def test_4_1_a_delivery_saves_every_field(self):
        batch = self.deliver()
        self.assertEqual(batch.medicine, self.medicine)
        self.assertEqual(batch.supplier, "Beirut Medical Supplies")
        self.assertEqual(batch.batch_number, "NEW-001")
        self.assertEqual(batch.expiry_date, date(2030, 6, 30))
        self.assertEqual(batch.quantity, 100)
        self.assertEqual(batch.unit_cost, Decimal("4.25"))

    def test_4_1_a_delivery_adds_to_the_available_stock(self):
        create_batch(self.medicine, quantity=8)
        self.deliver(quantity=100)
        self.assertEqual(available_stock(self.medicine), 108)

    def test_4_1_each_delivery_is_a_new_batch(self):
        self.deliver(batch_number="NEW-001")
        self.deliver(batch_number="NEW-002")
        self.assertEqual(self.medicine.batch_set.count(), 2)

    def test_4_1_expired_batches_are_not_available(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        create_batch(self.medicine, quantity=50, expiry_date=yesterday)
        self.assertEqual(available_stock(self.medicine), 0)

    def test_4_1_a_batch_expiring_today_is_available(self):
        create_batch(self.medicine, quantity=50, expiry_date=timezone.localdate())
        self.assertEqual(available_stock(self.medicine), 50)

    def test_4_1_a_medicine_with_no_batches_has_no_stock(self):
        self.assertEqual(available_stock(self.medicine), 0)

    def test_4_1_a_delivery_is_logged_with_who_recorded_it(self):
        self.deliver()
        entry = ActivityLog.objects.get()
        self.assertEqual(entry.user, self.karim)
        self.assertEqual(entry.action, "Recorded delivery")
        self.assertEqual(entry.details, "Amoxicillin 500 mg · batch NEW-001 · +100")

    def test_4_1_a_delivery_that_cannot_be_logged_saves_nothing(self):
        with patch("inventory.services.log_action", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self.deliver()
        self.assertFalse(Batch.objects.exists())


class StockListServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.medicine = create_medicine()

    def test_4_1_a_medicine_with_no_batches_still_has_a_row(self):
        rows = stock_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["medicine"], self.medicine)
        self.assertIsNone(rows[0]["batch"])

    def test_4_1_rows_are_in_medicine_name_order(self):
        aspirin = Medicine.objects.create(
            name="Aspirin",
            strength="81 mg",
            barcode="1000000000002",
            drug_class=self.medicine.drug_class,
            price=Decimal("3.50"),
            minimum_quantity=30,
        )
        self.assertEqual(
            [row["medicine"] for row in stock_rows()],
            [self.medicine, aspirin],
        )

    def test_4_1_only_medicines_with_available_stock_count_as_in_stock(self):
        create_medicine(barcode="1000000000002")
        expired_medicine = create_medicine(barcode="1000000000003")
        yesterday = timezone.localdate() - timedelta(days=1)
        create_batch(expired_medicine, expiry_date=yesterday)
        create_batch(self.medicine)
        self.assertEqual(medicines_in_stock_count(), 1)

    def test_4_1_deliveries_today_only_counts_batches_received_today(self):
        create_batch(self.medicine, batch_number="TODAY")
        old = create_batch(self.medicine, batch_number="OLD")
        Batch.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=3))
        self.assertEqual(deliveries_today_count(), 1)


class StockLookupServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.amoxicillin = create_medicine()
        cls.aspirin = create_aspirin()

    def setUp(self):
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def row_for(self, medicine):
        return next(row for row in stock_rows() if row["medicine"] == medicine)

    def test_4_2_a_medicine_with_several_batches_has_one_row(self):
        create_batch(self.amoxicillin, batch_number="AMX-001")
        create_batch(self.amoxicillin, batch_number="AMX-002")
        self.assertEqual(
            [row["medicine"] for row in stock_rows()],
            [self.amoxicillin, self.aspirin],
        )

    def test_4_2_the_row_shows_the_total_available_stock(self):
        create_batch(self.amoxicillin, batch_number="AMX-001", quantity=8)
        create_batch(self.amoxicillin, batch_number="AMX-002", quantity=100)
        self.assertEqual(self.row_for(self.amoxicillin)["available"], 108)

    def test_4_2_expired_batches_are_left_out_of_the_total(self):
        create_batch(self.amoxicillin, batch_number="OLD", quantity=25, expiry_date=self.yesterday)
        create_batch(self.amoxicillin, batch_number="NEW", quantity=200)
        self.assertEqual(self.row_for(self.amoxicillin)["available"], 200)

    def test_4_2_the_row_shows_the_batch_that_expires_first(self):
        create_batch(self.amoxicillin, batch_number="LATE", expiry_date=date(2031, 1, 31))
        soon = create_batch(self.amoxicillin, batch_number="SOON", expiry_date=date(2029, 1, 31))
        self.assertEqual(self.row_for(self.amoxicillin)["batch"], soon)

    def test_4_2_an_expired_batch_is_not_the_next_batch(self):
        create_batch(self.amoxicillin, batch_number="OLD", expiry_date=self.yesterday)
        new = create_batch(self.amoxicillin, batch_number="NEW")
        self.assertEqual(self.row_for(self.amoxicillin)["batch"], new)

    def test_4_2_an_empty_batch_is_not_the_next_batch(self):
        create_batch(self.amoxicillin, batch_number="EMPTY", quantity=0, expiry_date=date(2029, 1, 31))
        full = create_batch(self.amoxicillin, batch_number="FULL")
        self.assertEqual(self.row_for(self.amoxicillin)["batch"], full)

    def test_4_2_a_medicine_with_only_expired_stock_has_no_next_batch(self):
        create_batch(self.amoxicillin, batch_number="OLD", expiry_date=self.yesterday)
        row = self.row_for(self.amoxicillin)
        self.assertIsNone(row["batch"])
        self.assertEqual(row["available"], 0)

    def test_4_2_the_row_shows_the_latest_delivery(self):
        old = create_batch(self.amoxicillin, batch_number="OLD")
        Batch.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=3))
        new = create_batch(self.amoxicillin, batch_number="NEW")
        self.assertEqual(self.row_for(self.amoxicillin)["last_received"], new.received_at)

    def test_4_2_a_medicine_never_delivered_has_no_delivery_date(self):
        self.assertIsNone(self.row_for(self.aspirin)["last_received"])

    def test_4_2_search_finds_part_of_a_name_in_any_case(self):
        self.assertEqual([row["medicine"] for row in stock_rows("AMOX")], [self.amoxicillin])

    def test_4_2_search_finds_a_batch_number(self):
        create_batch(self.amoxicillin, batch_number="AMX-777")
        self.assertEqual([row["medicine"] for row in stock_rows("777")], [self.amoxicillin])

    def test_4_2_search_finds_a_barcode(self):
        self.assertEqual([row["medicine"] for row in stock_rows("2000000000001")], [self.aspirin])

    def test_4_2_a_medicine_matching_on_several_batches_appears_once(self):
        create_batch(self.amoxicillin, batch_number="AMX-001")
        create_batch(self.amoxicillin, batch_number="AMX-002")
        self.assertEqual([row["medicine"] for row in stock_rows("AMX")], [self.amoxicillin])

    def test_4_2_a_search_with_no_match_finds_nothing(self):
        self.assertEqual(stock_rows("zzz"), [])

    def test_4_2_medicine_count_counts_every_medicine(self):
        self.assertEqual(medicine_count(), 2)


class InventoryPagesTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.nadim = User.objects.create_user(username="nadim", password="pass", role="owner")
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")
        cls.karim = User.objects.create_user(username="karim", password="pass", role="assistant")
        cls.samira = User.objects.create_user(username="samira", password="pass", role="patient")
        cls.medicine = create_medicine()


class InventoryPageAccessTests(InventoryPagesTestData):
    def test_4_1_signed_out_visitors_go_to_the_staff_login(self):
        for address in ["/inventory/", "/inventory/delivery/"]:
            self.assertRedirects(self.client.get(address), f"/login/staff/?next={address}")

    def test_4_1_every_staff_role_can_open_the_pages(self):
        for user in [self.nadim, self.rana, self.karim]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/inventory/").status_code, 200)
            self.assertEqual(self.client.get("/inventory/delivery/").status_code, 200)

    def test_4_1_patient_side_users_are_refused(self):
        self.client.force_login(self.samira)
        self.assertEqual(self.client.get("/inventory/").status_code, 403)
        self.assertEqual(self.client.get("/inventory/delivery/").status_code, 403)


class InventoryPageTests(InventoryPagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)

    def test_4_1_page_uses_the_figma_wording(self):
        response = self.client.get("/inventory/")
        self.assertContains(response, "Pharmacist console")
        self.assertContains(response, "Stock List")
        self.assertContains(response, "Medicines in stock")
        self.assertContains(response, "Deliveries today")

    def test_4_1_the_list_shows_a_delivered_batch(self):
        create_batch(self.medicine, batch_number="AMX-777", quantity=64)
        response = self.client.get("/inventory/")
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "AMX-777")
        self.assertContains(response, "31 Jan 2030")
        self.assertContains(response, ">64<")

    def test_4_1_a_medicine_with_no_batches_says_no_stock_is_left(self):
        self.assertContains(self.client.get("/inventory/"), "No stock left")

    def test_4_1_the_cards_show_the_counts(self):
        create_batch(self.medicine)
        response = self.client.get("/inventory/")
        self.assertEqual(response.context["medicines_in_stock"], 1)
        self.assertEqual(response.context["deliveries_today"], 1)

    def test_4_1_the_page_links_to_recording_a_delivery(self):
        self.assertContains(self.client.get("/inventory/"), 'href="/inventory/delivery/"')

    def test_4_1_the_menu_links_to_the_inventory_page(self):
        self.assertContains(self.client.get("/inventory/"), 'href="/inventory/"')

    def test_4_1_the_popup_is_closed_on_the_inventory_page(self):
        self.assertNotContains(self.client.get("/inventory/"), "<dialog")


class StockLookupPageTests(InventoryPagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)
        self.aspirin = create_aspirin()

    def test_4_2_the_page_has_a_search_box(self):
        response = self.client.get("/inventory/")
        self.assertContains(response, 'name="search"')
        self.assertContains(response, 'placeholder="Search by medicine or batch"')

    def test_4_2_searching_narrows_the_list(self):
        response = self.client.get("/inventory/?search=asp")
        self.assertContains(response, "Aspirin 81 mg")
        self.assertNotContains(response, "Amoxicillin 500 mg")

    def test_4_2_the_search_stays_in_the_box(self):
        self.assertContains(self.client.get("/inventory/?search=asp"), 'value="asp"')

    def test_4_2_spaces_around_a_search_are_ignored(self):
        response = self.client.get("/inventory/?search=%20asp%20")
        self.assertContains(response, "Aspirin 81 mg")
        self.assertNotContains(response, "Amoxicillin 500 mg")

    def test_4_2_the_list_says_how_many_medicines_are_shown(self):
        self.assertContains(self.client.get("/inventory/?search=asp"), "Showing 1 of 2 medicines")

    def test_4_2_a_search_with_no_match_says_so(self):
        self.assertContains(self.client.get("/inventory/?search=zzz"), "No medicines found.")

    def test_4_2_the_cards_count_everything_while_searching(self):
        create_batch(self.medicine)
        create_batch(self.aspirin, batch_number="ASP-001")
        response = self.client.get("/inventory/?search=asp")
        self.assertEqual(response.context["medicines_in_stock"], 2)

    def test_4_2_the_list_shows_the_total_available_stock(self):
        create_batch(self.medicine, batch_number="AMX-001", quantity=40)
        create_batch(self.medicine, batch_number="AMX-002", quantity=60)
        self.assertContains(self.client.get("/inventory/"), ">100<")

    def test_4_2_the_list_leaves_out_expired_stock(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        create_batch(self.medicine, batch_number="OLD-001", quantity=25, expiry_date=yesterday)
        create_batch(self.medicine, batch_number="NEW-001", quantity=200)
        response = self.client.get("/inventory/")
        self.assertContains(response, "NEW-001")
        self.assertNotContains(response, "OLD-001")
        self.assertContains(response, ">200<")


class DeliveryPageTests(InventoryPagesTestData):
    def setUp(self):
        self.client.force_login(self.karim)
        self.delivery = {
            "medicine": self.medicine.pk,
            "supplier": "Beirut Medical Supplies",
            "batch_number": "NEW-001",
            "expiry_date": (timezone.localdate() + timedelta(days=365)).isoformat(),
            "quantity": 100,
            "unit_cost": "4.25",
        }

    def assert_nothing_was_saved(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Batch.objects.exists())
        self.assertFalse(ActivityLog.objects.exists())

    def test_4_1_the_popup_uses_the_figma_wording(self):
        response = self.client.get("/inventory/delivery/")
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "Enter the delivery once. Stock updates automatically.")
        self.assertContains(response, "Quantity received")
        self.assertContains(response, "Save Delivery")

    def test_4_1_the_popup_stays_on_top_of_the_stock_list(self):
        self.assertContains(self.client.get("/inventory/delivery/"), "Stock List")

    def test_4_1_cancelling_goes_back_to_the_inventory_page(self):
        self.assertContains(self.client.get("/inventory/delivery/"), 'href="/inventory/"')

    def test_4_1_a_valid_delivery_is_saved(self):
        response = self.client.post("/inventory/delivery/", self.delivery)
        self.assertRedirects(response, "/inventory/")
        batch = Batch.objects.get()
        self.assertEqual(batch.medicine, self.medicine)
        self.assertEqual(batch.supplier, "Beirut Medical Supplies")
        self.assertEqual(batch.batch_number, "NEW-001")
        self.assertEqual(batch.quantity, 100)
        self.assertEqual(batch.unit_cost, Decimal("4.25"))

    def test_4_1_the_delivery_is_logged_for_the_signed_in_user(self):
        self.client.post("/inventory/delivery/", self.delivery)
        self.assertEqual(ActivityLog.objects.get().user, self.karim)

    def test_4_1_a_saved_delivery_shows_a_confirmation(self):
        response = self.client.post("/inventory/delivery/", self.delivery, follow=True)
        self.assertContains(response, "Delivery recorded.")

    def test_4_1_a_quantity_of_zero_is_refused(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "quantity": 0})
        self.assert_nothing_was_saved(response)

    def test_4_1_a_negative_quantity_is_refused(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "quantity": -5})
        self.assert_nothing_was_saved(response)

    def test_4_1_a_unit_cost_of_zero_is_refused(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "unit_cost": "0.00"})
        self.assert_nothing_was_saved(response)

    def test_4_1_an_expiry_date_of_today_is_refused(self):
        today = timezone.localdate().isoformat()
        response = self.client.post("/inventory/delivery/", {**self.delivery, "expiry_date": today})
        self.assert_nothing_was_saved(response)
        self.assertContains(response, "The expiry date must be in the future.")

    def test_4_1_a_past_expiry_date_is_refused(self):
        response = self.client.post(
            "/inventory/delivery/",
            {**self.delivery, "expiry_date": "2020-01-01"},
        )
        self.assert_nothing_was_saved(response)

    def test_4_1_a_supplier_is_required(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "supplier": ""})
        self.assert_nothing_was_saved(response)

    def test_4_1_a_batch_number_is_required(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "batch_number": ""})
        self.assert_nothing_was_saved(response)

    def test_4_1_a_medicine_is_required(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "medicine": ""})
        self.assert_nothing_was_saved(response)

    def test_4_1_a_refused_delivery_keeps_the_popup_open(self):
        response = self.client.post("/inventory/delivery/", {**self.delivery, "quantity": 0})
        self.assertContains(response, "<dialog open")

    def test_4_1_a_patient_side_user_cannot_record_a_delivery(self):
        self.client.force_login(self.samira)
        response = self.client.post("/inventory/delivery/", self.delivery)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Batch.objects.exists())
