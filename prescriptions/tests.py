from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from passport.models import CurrentMedicine, Patient
from prescriptions.models import Prescription, PrescriptionItem
from prescriptions.services import (
    add_item,
    create_prescription,
    days_waiting,
    delete_prescription,
    mark_collected,
    mark_ready,
    mark_returned,
    ready_count,
    ready_rows,
    remove_item,
    start_preparing,
    uncollected_count,
    uncollected_rows,
)
from sales.models import Sale


def make_user(username, role, is_pharmacist=False):
    return get_user_model().objects.create(username=username, role=role, is_pharmacist=is_pharmacist)


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


class PrescriptionTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rana = make_user("rana", "pharmacist")
        cls.samira = Patient.objects.create(
            name="Samira",
            date_of_birth=date(1958, 3, 14),
            phone="03 100 001",
        )
        cls.amoxicillin = make_medicine("Amoxicillin", "1000000000001", "8.50")
        cls.metformin = make_medicine("Metformin", "1000000000004", "6.00")

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity

    def status_of(self, prescription):
        prescription.refresh_from_db()
        return prescription.status

    def prescription_with_items(self):
        prescription = create_prescription(self.samira)
        add_item(prescription, self.amoxicillin, 21, 7)
        add_item(prescription, self.metformin, 60, 30, long_term=True)
        return prescription


class PrescriptionModelTests(PrescriptionTestData):
    def test_1_1_a_new_prescription_is_received(self):
        prescription = create_prescription(self.samira)
        self.assertEqual(prescription.patient, self.samira)
        self.assertEqual(prescription.status, "received")
        self.assertIsNotNone(prescription.created_at)
        self.assertIsNone(prescription.ready_at)
        self.assertIsNone(prescription.collected_at)

    def test_1_1_the_statuses_follow_the_prescription_rules(self):
        values = [value for value, label in Prescription.STATUS_CHOICES]
        self.assertEqual(values, ["received", "preparing", "ready", "collected", "returned"])

    def test_1_1_an_item_saves_its_medicine_quantity_and_days_of_supply(self):
        add_item(create_prescription(self.samira), self.metformin, 60, 30, long_term=True)
        item = PrescriptionItem.objects.get()
        self.assertEqual(item.medicine, self.metformin)
        self.assertEqual(item.quantity, 60)
        self.assertEqual(item.days_of_supply, 30)
        self.assertTrue(item.long_term)
        self.assertIsNone(item.batch)

    def test_1_1_an_item_is_not_long_term_unless_chosen(self):
        add_item(create_prescription(self.samira), self.amoxicillin, 21, 7)
        self.assertFalse(PrescriptionItem.objects.get().long_term)

    def test_1_1_a_patient_with_a_prescription_cannot_be_deleted(self):
        create_prescription(self.samira)
        with self.assertRaises(ProtectedError):
            self.samira.delete()

    def test_1_1_a_medicine_on_a_prescription_cannot_be_deleted(self):
        add_item(create_prescription(self.samira), self.amoxicillin, 21, 7)
        with self.assertRaises(ProtectedError):
            self.amoxicillin.delete()

    def test_1_1_deleting_a_prescription_deletes_its_items(self):
        self.prescription_with_items().delete()
        self.assertFalse(PrescriptionItem.objects.exists())


class ReceivedPrescriptionTests(PrescriptionTestData):
    def setUp(self):
        self.prescription = create_prescription(self.samira)

    def test_1_1_medicines_are_added_one_at_a_time(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        add_item(self.prescription, self.metformin, 60, 30)
        items = self.prescription.prescriptionitem_set.order_by("id")
        self.assertEqual([item.medicine for item in items], [self.amoxicillin, self.metformin])

    def test_1_1_a_medicine_can_be_removed(self):
        item = add_item(self.prescription, self.amoxicillin, 21, 7)
        remove_item(item)
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_1_1_a_received_prescription_can_be_deleted(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        delete_prescription(self.prescription)
        self.assertFalse(Prescription.objects.exists())

    def test_1_1_preparing_starts_once_there_is_a_medicine(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        start_preparing(self.prescription)
        self.assertEqual(self.status_of(self.prescription), "preparing")

    def test_1_1_an_empty_prescription_cannot_start_preparing(self):
        with self.assertRaisesMessage(ValidationError, "Add at least one medicine first."):
            start_preparing(self.prescription)
        self.assertEqual(self.status_of(self.prescription), "received")

    def test_1_1_a_received_prescription_cannot_skip_to_ready(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        with self.assertRaisesMessage(ValidationError, "Only a Preparing prescription can be marked Ready."):
            mark_ready(self.rana, self.prescription)
        self.assertEqual(self.status_of(self.prescription), "received")

    def test_1_1_a_received_prescription_cannot_be_collected_or_returned(self):
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be marked Collected."):
            mark_collected(self.rana, self.prescription, "cash")
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be returned to stock."):
            mark_returned(self.rana, self.prescription)
        self.assertEqual(self.status_of(self.prescription), "received")


class PreparingPrescriptionTests(PrescriptionTestData):
    def setUp(self):
        self.prescription = create_prescription(self.samira)
        self.item = add_item(self.prescription, self.amoxicillin, 21, 7)
        start_preparing(self.prescription)

    def test_1_1_medicines_cannot_be_added_once_preparing(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Medicines can only be changed while the prescription is Received.",
        ):
            add_item(self.prescription, self.metformin, 60, 30)
        self.assertEqual(PrescriptionItem.objects.count(), 1)

    def test_1_1_medicines_cannot_be_removed_once_preparing(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Medicines can only be changed while the prescription is Received.",
        ):
            remove_item(self.item)
        self.assertTrue(PrescriptionItem.objects.exists())

    def test_1_1_a_preparing_prescription_cannot_be_deleted(self):
        with self.assertRaisesMessage(ValidationError, "Only a Received prescription can be deleted."):
            delete_prescription(self.prescription)
        self.assertTrue(Prescription.objects.exists())

    def test_1_1_preparing_cannot_start_twice(self):
        with self.assertRaisesMessage(ValidationError, "Only a Received prescription can start preparing."):
            start_preparing(self.prescription)

    def test_1_1_a_preparing_prescription_cannot_be_collected_yet(self):
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be marked Collected."):
            mark_collected(self.rana, self.prescription, "cash")
        self.assertEqual(self.status_of(self.prescription), "preparing")


class MarkReadyTests(PrescriptionTestData):
    def setUp(self):
        self.later = make_batch(self.amoxicillin, "AMX-LATE", quantity=100, days_until_expiry=300)
        self.first = make_batch(self.amoxicillin, "AMX-FIRST", quantity=50, days_until_expiry=100)
        self.metformin_batch = make_batch(self.metformin, "MET-001", quantity=100)
        self.prescription = self.prescription_with_items()
        start_preparing(self.prescription)

    def test_1_1_ready_sets_the_status_and_time(self):
        mark_ready(self.rana, self.prescription)
        self.prescription.refresh_from_db()
        self.assertEqual(self.prescription.status, "ready")
        self.assertIsNotNone(self.prescription.ready_at)

    def test_1_1_ready_takes_stock_from_the_batch_that_expires_first(self):
        mark_ready(self.rana, self.prescription)
        self.assertEqual(self.quantity_of(self.first), 29)
        self.assertEqual(self.quantity_of(self.later), 100)
        self.assertEqual(self.quantity_of(self.metformin_batch), 40)

    def test_1_1_each_item_keeps_the_batch_it_was_taken_from(self):
        mark_ready(self.rana, self.prescription)
        batches = [item.batch for item in PrescriptionItem.objects.order_by("id")]
        self.assertEqual(batches, [self.first, self.metformin_batch])

    def test_1_1_taking_stock_is_logged_for_the_pharmacist(self):
        mark_ready(self.rana, self.prescription)
        entries = ActivityLog.objects.filter(action="Took stock")
        self.assertEqual(entries.count(), 2)
        self.assertEqual({entry.user for entry in entries}, {self.rana})

    def test_1_1_not_enough_stock_in_one_batch_changes_nothing(self):
        self.metformin_batch.quantity = 10
        self.metformin_batch.save()
        with self.assertRaisesMessage(ValidationError, "No single batch of Metformin 500 mg has 60."):
            mark_ready(self.rana, self.prescription)
        self.prescription.refresh_from_db()
        self.assertEqual(self.prescription.status, "preparing")
        self.assertIsNone(self.prescription.ready_at)
        self.assertEqual(self.quantity_of(self.first), 50)
        self.assertFalse(PrescriptionItem.objects.filter(batch__isnull=False).exists())

    def test_1_1_ready_cannot_happen_twice(self):
        mark_ready(self.rana, self.prescription)
        with self.assertRaisesMessage(ValidationError, "Only a Preparing prescription can be marked Ready."):
            mark_ready(self.rana, self.prescription)
        self.assertEqual(self.quantity_of(self.first), 29)


class MarkCollectedTests(PrescriptionTestData):
    def setUp(self):
        make_batch(self.amoxicillin, "AMX-001")
        self.metformin_batch = make_batch(self.metformin, "MET-001")
        self.prescription = self.prescription_with_items()
        start_preparing(self.prescription)
        mark_ready(self.rana, self.prescription)

    def test_1_1_collected_sets_the_status_and_time(self):
        mark_collected(self.rana, self.prescription, "cash")
        self.prescription.refresh_from_db()
        self.assertEqual(self.prescription.status, "collected")
        self.assertIsNotNone(self.prescription.collected_at)

    def test_1_1_collecting_records_a_prescription_sale(self):
        mark_collected(self.rana, self.prescription, "card")
        sale = Sale.objects.get()
        self.assertEqual(sale.staff, self.rana)
        self.assertEqual(sale.payment_method, "card")
        self.assertEqual(sale.patient, self.samira)
        self.assertEqual(sale.source, "prescription")

    def test_1_1_the_sale_uses_the_current_prices(self):
        self.amoxicillin.price = Decimal("9.00")
        self.amoxicillin.save()
        mark_collected(self.rana, self.prescription, "cash")
        lines = Sale.objects.get().saleitem_set.order_by("id")
        self.assertEqual(
            [(line.medicine, line.quantity, line.unit_price) for line in lines],
            [(self.amoxicillin, 21, Decimal("9.00")), (self.metformin, 60, Decimal("6.00"))],
        )

    def test_1_1_collecting_does_not_touch_stock(self):
        before = self.quantity_of(self.metformin_batch)
        mark_collected(self.rana, self.prescription, "cash")
        self.assertEqual(self.quantity_of(self.metformin_batch), before)

    def test_1_1_long_term_medicines_join_the_current_medicines(self):
        mark_collected(self.rana, self.prescription, "cash")
        current = CurrentMedicine.objects.filter(patient=self.samira)
        self.assertEqual([entry.medicine for entry in current], [self.metformin])

    def test_1_1_a_current_medicine_is_not_added_twice(self):
        CurrentMedicine.objects.create(patient=self.samira, medicine=self.metformin)
        mark_collected(self.rana, self.prescription, "cash")
        self.assertEqual(CurrentMedicine.objects.filter(patient=self.samira).count(), 1)

    def test_1_1_collected_is_final(self):
        mark_collected(self.rana, self.prescription, "cash")
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be marked Collected."):
            mark_collected(self.rana, self.prescription, "cash")
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be returned to stock."):
            mark_returned(self.rana, self.prescription)
        self.assertEqual(Sale.objects.count(), 1)


class MarkReturnedTests(PrescriptionTestData):
    def setUp(self):
        self.batch = make_batch(self.amoxicillin, "AMX-001", quantity=100)
        self.prescription = create_prescription(self.samira)
        add_item(self.prescription, self.amoxicillin, 21, 7)
        start_preparing(self.prescription)
        mark_ready(self.rana, self.prescription)

    def test_1_1_returned_sets_the_status(self):
        mark_returned(self.rana, self.prescription)
        self.assertEqual(self.status_of(self.prescription), "returned")

    def test_1_1_returned_stock_goes_back_to_the_same_batch(self):
        sooner = make_batch(self.amoxicillin, "AMX-SOON", quantity=100, days_until_expiry=50)
        mark_returned(self.rana, self.prescription)
        self.assertEqual(self.quantity_of(self.batch), 100)
        self.assertEqual(self.quantity_of(sooner), 100)

    def test_1_1_returning_is_logged_for_the_pharmacist(self):
        mark_returned(self.rana, self.prescription)
        self.assertEqual(ActivityLog.objects.get(action="Returned stock").user, self.rana)

    def test_1_1_returned_is_final(self):
        mark_returned(self.rana, self.prescription)
        with self.assertRaisesMessage(ValidationError, "Only a Ready prescription can be marked Collected."):
            mark_collected(self.rana, self.prescription, "cash")
        self.assertFalse(Sale.objects.exists())


class PrescriptionRowsTests(PrescriptionTestData):
    def test_1_1_the_list_is_newest_first(self):
        older = create_prescription(self.samira)
        Prescription.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=1))
        newer = create_prescription(self.samira)
        self.assertEqual([row["prescription"] for row in uncollected_rows()], [newer, older])

    def test_1_1_each_row_lists_its_medicines(self):
        self.prescription_with_items()
        items = uncollected_rows()[0]["items"]
        self.assertEqual([item.medicine for item in items], [self.amoxicillin, self.metformin])


class UncollectedListTests(PrescriptionTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2), phone="03 100 004")

    def listed(self, search=""):
        return [row["prescription"] for row in uncollected_rows(search)]

    def test_1_2_received_preparing_and_ready_are_listed(self):
        waiting = [
            Prescription.objects.create(patient=self.samira, status=status)
            for status in ["received", "preparing", "ready"]
        ]
        self.assertCountEqual(self.listed(), waiting)

    def test_1_2_collected_and_returned_are_left_out(self):
        for status in ["collected", "returned"]:
            Prescription.objects.create(patient=self.samira, status=status)
        self.assertEqual(self.listed(), [])

    def test_1_2_the_count_leaves_out_collected_and_returned(self):
        for status in ["received", "preparing", "ready", "collected", "returned"]:
            Prescription.objects.create(patient=self.samira, status=status)
        self.assertEqual(uncollected_count(), 3)

    def test_1_2_search_finds_part_of_a_name_in_any_case(self):
        samiras = create_prescription(self.samira)
        create_prescription(self.ahmad)
        self.assertEqual(self.listed("sAM"), [samiras])

    def test_1_2_search_finds_part_of_a_phone(self):
        ahmads = create_prescription(self.ahmad)
        create_prescription(self.samira)
        self.assertEqual(self.listed("100 004"), [ahmads])

    def test_1_2_search_leaves_out_collected_prescriptions(self):
        Prescription.objects.create(patient=self.samira, status="collected")
        self.assertEqual(self.listed("Samira"), [])

    def test_1_2_search_with_no_match_finds_nothing(self):
        create_prescription(self.samira)
        self.assertEqual(self.listed("Nour"), [])


class PrescriptionPagesTestData(PrescriptionTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nadim = make_user("nadim", "owner", is_pharmacist=True)
        cls.hala = make_user("hala", "owner")
        cls.karim = make_user("karim", "assistant")
        cls.maya = make_user("maya", "patient")


class PrescriptionPageAccessTests(PrescriptionPagesTestData):
    def setUp(self):
        self.prescription = create_prescription(self.samira)
        add_item(self.prescription, self.amoxicillin, 21, 7)
        self.address = f"/prescriptions/{self.prescription.pk}/"

    def test_1_1_signed_out_visitors_go_to_the_staff_login(self):
        for address in ["/prescriptions/", "/prescriptions/new/", self.address]:
            self.assertRedirects(self.client.get(address), f"/login/staff/?next={address}")

    def test_1_1_patient_side_users_are_refused(self):
        self.client.force_login(self.maya)
        for address in ["/prescriptions/", "/prescriptions/new/", self.address]:
            self.assertEqual(self.client.get(address).status_code, 403)

    def test_1_1_every_staff_role_can_see_the_list_and_a_prescription(self):
        for user in [self.nadim, self.hala, self.rana, self.karim]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/prescriptions/").status_code, 200)
            self.assertEqual(self.client.get(self.address).status_code, 200)

    def test_1_1_an_owner_who_is_a_pharmacist_can_create_a_prescription(self):
        self.client.force_login(self.nadim)
        self.assertEqual(self.client.get("/prescriptions/new/").status_code, 200)

    def test_1_1_an_owner_who_is_not_a_pharmacist_cannot_create_one(self):
        self.client.force_login(self.hala)
        self.assertEqual(self.client.get("/prescriptions/new/").status_code, 403)

    def test_1_1_an_assistant_cannot_create_a_prescription(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.client.get("/prescriptions/new/").status_code, 403)
        response = self.client.post("/prescriptions/new/", {"patient": self.samira.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Prescription.objects.count(), 1)

    def test_1_1_an_assistant_cannot_change_a_prescription(self):
        self.client.force_login(self.karim)
        item = PrescriptionItem.objects.get()
        addresses = [
            f"{self.address}items/",
            f"/prescriptions/items/{item.pk}/remove/",
            f"{self.address}preparing/",
            f"{self.address}ready/",
            f"{self.address}collected/",
            f"{self.address}returned/",
            f"{self.address}delete/",
        ]
        for address in addresses:
            self.assertEqual(self.client.post(address).status_code, 403)
        self.assertEqual(self.status_of(self.prescription), "received")
        self.assertEqual(PrescriptionItem.objects.count(), 1)


class PrescriptionListPageTests(PrescriptionPagesTestData):
    def setUp(self):
        self.client.force_login(self.rana)

    def test_1_1_page_uses_the_figma_wording(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, "Pharmacist console")
        self.assertContains(response, "Prescriptions")
        self.assertContains(
            response,
            "Every prescription, reservation and refill request, from received to collected.",
        )
        self.assertContains(response, "New Prescription")

    def test_1_1_the_menu_links_to_the_prescriptions_page(self):
        self.assertContains(self.client.get("/prescriptions/"), 'href="/prescriptions/"')

    def test_1_1_a_row_shows_the_patient_medicines_and_status(self):
        self.prescription_with_items()
        response = self.client.get("/prescriptions/")
        self.assertContains(response, "Samira")
        self.assertContains(response, "03 100 001")
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, "+ 1 more")
        self.assertContains(response, "Received")

    def test_1_1_each_row_opens_its_prescription(self):
        prescription = create_prescription(self.samira)
        self.assertContains(self.client.get("/prescriptions/"), f'href="/prescriptions/{prescription.pk}/"')

    def test_1_1_each_status_shows_its_next_step(self):
        for status in ["preparing", "ready"]:
            Prescription.objects.create(patient=self.samira, status=status)
        response = self.client.get("/prescriptions/")
        for words in ["Mark Ready", "Mark Collected", "Preparing", "Ready"]:
            self.assertContains(response, words)

    def test_1_1_an_empty_list_says_so(self):
        self.assertContains(self.client.get("/prescriptions/"), "No uncollected prescriptions.")

    def test_1_1_an_assistant_does_not_see_the_new_prescription_button(self):
        self.client.force_login(self.karim)
        self.assertNotContains(self.client.get("/prescriptions/"), 'href="/prescriptions/new/"')


class UncollectedListPageTests(PrescriptionPagesTestData):
    def setUp(self):
        self.client.force_login(self.rana)
        self.ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2), phone="03 100 004")

    def test_1_2_collected_and_returned_prescriptions_are_not_listed(self):
        Prescription.objects.create(patient=self.ahmad, status="collected")
        Prescription.objects.create(patient=self.ahmad, status="returned")
        self.assertNotContains(self.client.get("/prescriptions/"), "Ahmad")

    def test_1_2_the_page_has_the_figma_search_box(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, 'name="search"')
        self.assertContains(response, 'placeholder="Search by patient name or phone"')

    def test_1_2_the_tab_counts_every_uncollected_prescription(self):
        create_prescription(self.samira)
        create_prescription(self.ahmad)
        Prescription.objects.create(patient=self.ahmad, status="collected")
        response = self.client.get("/prescriptions/", {"search": "Samira"})
        self.assertContains(response, "Prescriptions 2")
        self.assertContains(response, "Showing 1 of 2 prescriptions")

    def test_1_2_searching_by_name_shows_only_that_patient(self):
        create_prescription(self.samira)
        create_prescription(self.ahmad)
        response = self.client.get("/prescriptions/", {"search": "ahm"})
        self.assertContains(response, "Ahmad")
        self.assertNotContains(response, "Samira")

    def test_1_2_searching_by_phone_shows_only_that_patient(self):
        create_prescription(self.samira)
        create_prescription(self.ahmad)
        response = self.client.get("/prescriptions/", {"search": "100 001"})
        self.assertContains(response, "Samira")
        self.assertNotContains(response, "Ahmad")

    def test_1_2_the_box_keeps_the_search(self):
        self.assertContains(self.client.get("/prescriptions/", {"search": "ahm"}), 'value="ahm"')

    def test_1_2_a_search_with_no_match_says_so(self):
        create_prescription(self.samira)
        response = self.client.get("/prescriptions/", {"search": "Nour"})
        self.assertContains(response, 'No prescriptions match "Nour".')

    def test_1_2_an_assistant_can_search(self):
        self.client.force_login(self.karim)
        create_prescription(self.ahmad)
        self.assertContains(self.client.get("/prescriptions/", {"search": "ahm"}), "Ahmad")


class NewPrescriptionPageTests(PrescriptionPagesTestData):
    def setUp(self):
        self.client.force_login(self.rana)

    def test_1_1_the_popup_asks_for_the_patient(self):
        response = self.client.get("/prescriptions/new/")
        self.assertContains(response, "<dialog open")
        self.assertContains(response, 'name="patient"')
        self.assertContains(response, "Samira")
        self.assertContains(response, "Create Prescription")

    def test_1_1_the_popup_stays_on_top_of_the_list(self):
        response = self.client.get("/prescriptions/new/")
        self.assertContains(
            response,
            "Every prescription, reservation and refill request, from received to collected.",
        )

    def test_1_1_cancelling_goes_back_to_the_list(self):
        self.assertContains(self.client.get("/prescriptions/new/"), 'href="/prescriptions/"')

    def test_1_1_creating_a_prescription_opens_it(self):
        response = self.client.post("/prescriptions/new/", {"patient": self.samira.pk})
        prescription = Prescription.objects.get()
        self.assertRedirects(response, f"/prescriptions/{prescription.pk}/")
        self.assertEqual(prescription.patient, self.samira)
        self.assertEqual(prescription.status, "received")

    def test_1_1_a_patient_is_required(self):
        response = self.client.post("/prescriptions/new/", {"patient": ""})
        self.assertContains(response, "<dialog open")
        self.assertFalse(Prescription.objects.exists())


class PrescriptionPageTests(PrescriptionPagesTestData):
    def setUp(self):
        self.client.force_login(self.rana)
        self.batch = make_batch(self.amoxicillin, "AMX-001", quantity=100)
        self.prescription = create_prescription(self.samira)
        self.address = f"/prescriptions/{self.prescription.pk}/"
        self.new_item = {
            "medicine": self.amoxicillin.pk,
            "quantity": 21,
            "days_of_supply": 7,
            "long_term": "on",
        }

    def make_ready(self, quantity=21):
        add_item(self.prescription, self.amoxicillin, quantity, 7)
        start_preparing(self.prescription)
        mark_ready(self.rana, self.prescription)

    def test_1_1_the_page_shows_the_patient_and_status(self):
        response = self.client.get(self.address)
        self.assertContains(response, "Samira")
        self.assertContains(response, "Received")

    def test_1_1_an_unknown_prescription_is_not_found(self):
        self.assertEqual(self.client.get("/prescriptions/999/").status_code, 404)

    def test_1_1_a_pharmacist_can_add_a_medicine(self):
        response = self.client.post(f"{self.address}items/", self.new_item)
        self.assertRedirects(response, self.address)
        item = PrescriptionItem.objects.get()
        self.assertEqual(item.prescription, self.prescription)
        self.assertEqual(item.medicine, self.amoxicillin)
        self.assertEqual(item.quantity, 21)
        self.assertEqual(item.days_of_supply, 7)
        self.assertTrue(item.long_term)

    def test_1_1_the_added_medicine_is_listed(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        response = self.client.get(self.address)
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, ">21<")

    def test_1_1_a_quantity_of_zero_is_refused(self):
        response = self.client.post(f"{self.address}items/", {**self.new_item, "quantity": 0}, follow=True)
        self.assertContains(response, "Check the medicine, quantity and days of supply.")
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_1_1_a_pharmacist_can_remove_a_medicine(self):
        item = add_item(self.prescription, self.amoxicillin, 21, 7)
        response = self.client.post(f"/prescriptions/items/{item.pk}/remove/")
        self.assertRedirects(response, self.address)
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_1_1_a_pharmacist_can_delete_a_received_prescription(self):
        response = self.client.post(f"{self.address}delete/")
        self.assertRedirects(response, "/prescriptions/")
        self.assertFalse(Prescription.objects.exists())

    def test_1_1_deleting_shows_a_confirmation(self):
        response = self.client.post(f"{self.address}delete/", follow=True)
        self.assertContains(response, "Prescription deleted.")

    def test_1_1_the_add_form_shows_only_while_received(self):
        self.assertContains(self.client.get(self.address), "Add Medicine")
        add_item(self.prescription, self.amoxicillin, 21, 7)
        start_preparing(self.prescription)
        self.assertNotContains(self.client.get(self.address), "Add Medicine")

    def test_1_1_the_buttons_follow_the_status(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        response = self.client.get(self.address)
        self.assertContains(response, "Start Preparing")
        self.assertContains(response, "Delete Prescription")
        start_preparing(self.prescription)
        response = self.client.get(self.address)
        self.assertContains(response, "Mark Ready")
        self.assertNotContains(response, "Start Preparing")
        mark_ready(self.rana, self.prescription)
        response = self.client.get(self.address)
        self.assertContains(response, "Mark Collected")
        self.assertContains(response, "Return to Stock")

    def test_1_1_an_assistant_sees_no_buttons(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        self.client.force_login(self.karim)
        response = self.client.get(self.address)
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertNotContains(response, "Add Medicine")
        self.assertNotContains(response, "Start Preparing")
        self.assertNotContains(response, "Delete Prescription")

    def test_1_1_start_preparing_from_the_page(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        response = self.client.post(f"{self.address}preparing/")
        self.assertRedirects(response, self.address)
        self.assertEqual(self.status_of(self.prescription), "preparing")

    def test_1_1_an_empty_prescription_says_why_it_cannot_start(self):
        response = self.client.post(f"{self.address}preparing/", follow=True)
        self.assertContains(response, "Add at least one medicine first.")
        self.assertEqual(self.status_of(self.prescription), "received")

    def test_1_1_mark_ready_from_the_page(self):
        add_item(self.prescription, self.amoxicillin, 21, 7)
        start_preparing(self.prescription)
        response = self.client.post(f"{self.address}ready/")
        self.assertRedirects(response, self.address)
        self.assertEqual(self.status_of(self.prescription), "ready")
        self.assertEqual(self.quantity_of(self.batch), 79)

    def test_1_1_not_enough_stock_is_shown_on_the_page(self):
        add_item(self.prescription, self.amoxicillin, 500, 7)
        start_preparing(self.prescription)
        response = self.client.post(f"{self.address}ready/", follow=True)
        self.assertContains(response, "No single batch of Amoxicillin 500 mg has 500.")
        self.assertEqual(self.status_of(self.prescription), "preparing")

    def test_1_1_a_ready_prescription_shows_its_batch(self):
        self.make_ready()
        self.assertContains(self.client.get(self.address), "AMX-001")

    def test_1_1_mark_collected_records_the_payment_method(self):
        self.make_ready()
        response = self.client.post(f"{self.address}collected/", {"payment_method": "card"})
        self.assertRedirects(response, self.address)
        self.assertEqual(self.status_of(self.prescription), "collected")
        self.assertEqual(Sale.objects.get().payment_method, "card")

    def test_1_1_a_payment_method_is_required(self):
        self.make_ready()
        response = self.client.post(f"{self.address}collected/", {}, follow=True)
        self.assertContains(response, "Choose cash or card.")
        self.assertEqual(self.status_of(self.prescription), "ready")
        self.assertFalse(Sale.objects.exists())

    def test_1_1_return_to_stock_from_the_page(self):
        self.make_ready()
        response = self.client.post(f"{self.address}returned/")
        self.assertRedirects(response, self.address)
        self.assertEqual(self.status_of(self.prescription), "returned")
        self.assertEqual(self.quantity_of(self.batch), 100)

    def test_1_1_a_finished_prescription_has_no_buttons(self):
        self.make_ready()
        mark_collected(self.rana, self.prescription, "cash")
        response = self.client.get(self.address)
        self.assertNotContains(response, "Mark Collected")
        self.assertNotContains(response, "Return to Stock")


class ReadyOrdersTestData(PrescriptionPagesTestData):
    def setUp(self):
        self.client.force_login(self.rana)
        self.ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2), phone="03 100 004")

    def make_ready(self, patient, hours_ago=1):
        ready_at = timezone.now() - timedelta(hours=hours_ago)
        return Prescription.objects.create(patient=patient, status="ready", ready_at=ready_at)


class ReadyOrdersTests(ReadyOrdersTestData):
    def test_1_3_only_ready_orders_are_listed(self):
        ready = self.make_ready(self.samira)
        for status in ["received", "preparing", "collected", "returned"]:
            Prescription.objects.create(patient=self.ahmad, status=status)
        self.assertEqual([row["prescription"] for row in ready_rows()], [ready])
        self.assertEqual(ready_count(), 1)

    def test_1_3_the_oldest_ready_order_comes_first(self):
        older = self.make_ready(self.samira, hours_ago=48)
        newer = self.make_ready(self.ahmad, hours_ago=2)
        self.assertEqual([row["prescription"] for row in ready_rows()], [older, newer])

    def test_1_3_orders_can_be_searched_by_patient_name_or_phone(self):
        samira_order = self.make_ready(self.samira)
        ahmad_order = self.make_ready(self.ahmad)
        self.assertEqual([row["prescription"] for row in ready_rows("sam")], [samira_order])
        self.assertEqual([row["prescription"] for row in ready_rows("100 004")], [ahmad_order])

    def test_1_3_days_waiting_counts_from_the_day_it_became_ready(self):
        self.assertEqual(days_waiting(self.make_ready(self.samira, hours_ago=72)), 3)
        self.assertEqual(days_waiting(self.make_ready(self.ahmad, hours_ago=0)), 0)

    def test_1_3_each_row_carries_its_days_waiting(self):
        self.make_ready(self.samira, hours_ago=48)
        self.assertEqual(ready_rows()[0]["days_waiting"], 2)


class ReadyOrdersPageTests(ReadyOrdersTestData):
    def test_1_3_the_not_collected_tab_counts_ready_orders(self):
        self.make_ready(self.samira)
        self.make_ready(self.ahmad)
        response = self.client.get("/prescriptions/")
        self.assertContains(response, "Not collected 2")
        self.assertContains(response, 'href="/prescriptions/?show=ready')

    def test_1_3_the_list_shows_the_patient_medicine_and_ready_time(self):
        ready = self.make_ready(self.samira)
        PrescriptionItem.objects.create(prescription=ready, medicine=self.amoxicillin, quantity=2, days_of_supply=5)
        response = self.client.get("/prescriptions/", {"show": "ready"})
        self.assertContains(response, "Ready since")
        self.assertContains(response, "Samira")
        self.assertContains(response, "Amoxicillin 500 mg")
        self.assertContains(response, f'href="/prescriptions/{ready.pk}/"')
        self.assertContains(response, timezone.localtime(ready.ready_at).strftime("%d %b %Y, %H:%M"))

    def test_1_3_each_order_says_how_long_it_has_waited(self):
        self.make_ready(self.samira, hours_ago=72)
        self.assertContains(self.client.get("/prescriptions/", {"show": "ready"}), "Not collected for 3 days")

    def test_1_3_one_day_is_written_in_the_singular(self):
        self.make_ready(self.samira, hours_ago=24)
        response = self.client.get("/prescriptions/", {"show": "ready"})
        self.assertContains(response, "Not collected for 1 day")
        self.assertNotContains(response, "1 days")

    def test_1_3_an_order_ready_today_says_so(self):
        self.make_ready(self.samira, hours_ago=0)
        self.assertContains(self.client.get("/prescriptions/", {"show": "ready"}), "Ready today")

    def test_1_3_the_days_waited_are_only_in_the_not_collected_list(self):
        self.make_ready(self.samira, hours_ago=72)
        self.assertNotContains(self.client.get("/prescriptions/"), "Not collected for")

    def test_1_3_searching_keeps_the_not_collected_list(self):
        self.make_ready(self.samira)
        self.make_ready(self.ahmad)
        response = self.client.get("/prescriptions/", {"show": "ready", "search": "Samira"})
        self.assertEqual(response.context["show"], "ready")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["ready_count"], 2)
        self.assertContains(response, 'name="show" value="ready"')

    def test_1_3_one_prescription_is_written_in_the_singular(self):
        self.make_ready(self.samira)
        response = self.client.get("/prescriptions/", {"show": "ready"})
        self.assertContains(response, '<p class="stock-count">Showing 1 of 1 prescription</p>', html=True)

    def test_1_3_an_empty_list_says_so(self):
        self.assertContains(self.client.get("/prescriptions/", {"show": "ready"}), "No prescriptions are ready for pickup.")

    def test_1_3_a_search_with_no_match_says_so(self):
        self.make_ready(self.samira)
        response = self.client.get("/prescriptions/", {"show": "ready", "search": "Nour"})
        self.assertContains(response, 'No prescriptions match "Nour".')

    def test_1_3_patient_side_users_are_refused(self):
        self.client.force_login(self.maya)
        self.assertEqual(self.client.get("/prescriptions/", {"show": "ready"}).status_code, 403)
