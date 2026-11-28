from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from accounts.models import CaregiverLink
from core.models import ActivityLog, Notification
from core.services import notify
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from prescriptions.models import Prescription, PrescriptionItem
from prescriptions.services import delete_prescription, patient_long_term_item
from reservations.models import RefillRequest, Reservation
from reservations.services import (
    active_reservation_count,
    approve_refill,
    collect_reservation,
    delay_refill,
    expire_reservations,
    medicine_availability,
    minutes_left,
    new_refill_count,
    open_refill_count,
    patient_refill_requests,
    patient_reservation,
    pickup_deadline,
    refill_in_progress,
    refill_reminders,
    refill_rows,
    request_refill,
    reservation_rows,
    reserve_medicine,
    runs_out_on,
    runs_out_text,
    time_left_text,
)
from sales.models import Sale


def make_medicine(name, barcode, price):
    drug_class, created = DrugClass.objects.get_or_create(name="Test class")
    return Medicine.objects.create(
        name=name,
        strength="500 mg",
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal(price),
        minimum_quantity=5,
    )


def make_batch(medicine, batch_number, quantity, days_until_expiry=300):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=timezone.localdate() + timedelta(days=days_until_expiry),
        quantity=quantity,
        unit_cost=Decimal("2.00"),
        supplier="Cedar Medical Supplies",
    )


def reserved_minutes_ago(reservation, minutes):
    Reservation.objects.filter(pk=reservation.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))
    reservation.refresh_from_db()
    return reservation


class ReservationTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.maya_user = User.objects.create(username="maya", role="patient")
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.maya = Patient.objects.create(
            name="Maya",
            date_of_birth=date(1988, 9, 21),
            phone="03 100 002",
            user=cls.maya_user,
        )
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14), phone="03 100 001")
        cls.syrup = make_medicine("Paracetamol syrup", "1000000000017", "4.50")
        cls.cetirizine = make_medicine("Cetirizine", "1000000000011", "5.00")

    def setUp(self):
        self.syrup_batch = make_batch(self.syrup, "SYR-001", 10)
        self.cetirizine_batch = make_batch(self.cetirizine, "CET-001", 10)

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity

    def reserve(self, patient=None, medicine=None, quantity=2):
        return reserve_medicine(self.maya_user, patient or self.maya, medicine or self.syrup, quantity)


class ReserveMedicineTests(ReservationTestData):
    def test_10_2_a_new_reservation_is_active_with_its_batch(self):
        reservation = self.reserve()
        self.assertEqual(reservation.status, "active")
        self.assertEqual((reservation.patient, reservation.medicine, reservation.quantity), (self.maya, self.syrup, 2))
        self.assertEqual(reservation.batch, self.syrup_batch)

    def test_10_2_reserving_takes_the_stock(self):
        self.reserve()
        self.assertEqual(self.quantity_of(self.syrup_batch), 8)

    def test_10_2_reserving_uses_the_batch_that_expires_first(self):
        sooner = make_batch(self.syrup, "SYR-000", 5, days_until_expiry=30)
        self.assertEqual(self.reserve().batch, sooner)
        self.assertEqual(self.quantity_of(sooner), 3)

    def test_10_2_no_single_batch_with_enough_is_refused_and_nothing_changes(self):
        with self.assertRaises(ValidationError):
            self.reserve(quantity=11)
        self.assertFalse(Reservation.objects.exists())
        self.assertEqual(self.quantity_of(self.syrup_batch), 10)

    def test_10_2_reserving_is_logged_with_who_did_it(self):
        self.reserve()
        entry = ActivityLog.objects.get(action="Took stock")
        self.assertEqual(entry.user, self.maya_user)

    def test_10_2_a_reservation_is_held_for_2_hours(self):
        reservation = self.reserve()
        self.assertEqual(minutes_left(reservation, reservation.created_at), 120)


class TimeLeftTests(ReservationTestData):
    def test_10_3_minutes_left_counts_down(self):
        reservation = self.reserve()
        self.assertEqual(minutes_left(reservation, reservation.created_at + timedelta(minutes=28)), 92)

    def test_10_3_a_part_minute_counts_as_a_whole_one(self):
        reservation = self.reserve()
        self.assertEqual(minutes_left(reservation, reservation.created_at + timedelta(seconds=30)), 120)

    def test_10_3_minutes_left_never_go_below_zero(self):
        reservation = self.reserve()
        self.assertEqual(minutes_left(reservation, reservation.created_at + timedelta(hours=3)), 0)

    def test_10_3_time_left_is_written_in_hours_and_minutes(self):
        self.assertEqual(time_left_text(92), "1 h 32 min left")
        self.assertEqual(time_left_text(65), "1 h 05 min left")
        self.assertEqual(time_left_text(120), "2 h 00 min left")
        self.assertEqual(time_left_text(12), "12 min left")


class ExpireReservationsTests(ReservationTestData):
    def test_t7_a_reservation_older_than_2_hours_expires(self):
        reservation = reserved_minutes_ago(self.reserve(), 121)
        expire_reservations()
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "expired")
        self.assertEqual(self.quantity_of(self.syrup_batch), 10)

    def test_t7_a_reservation_younger_than_2_hours_stays_active(self):
        reservation = reserved_minutes_ago(self.reserve(), 119)
        expire_reservations()
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "active")
        self.assertEqual(self.quantity_of(self.syrup_batch), 8)

    def test_t7_the_stock_goes_back_to_the_batch_it_came_from(self):
        sooner = make_batch(self.syrup, "SYR-000", 5, days_until_expiry=30)
        reserved_minutes_ago(self.reserve(), 121)
        expire_reservations()
        self.assertEqual(self.quantity_of(sooner), 5)
        self.assertEqual(self.quantity_of(self.syrup_batch), 10)

    def test_t7_the_return_is_logged_as_the_system(self):
        reserved_minutes_ago(self.reserve(), 121)
        expire_reservations()
        entry = ActivityLog.objects.get(action="Returned stock")
        self.assertIsNone(entry.user)

    def test_t7_expiring_again_returns_nothing_more(self):
        reserved_minutes_ago(self.reserve(), 121)
        expire_reservations()
        expire_reservations()
        self.assertEqual(self.quantity_of(self.syrup_batch), 10)
        self.assertEqual(ActivityLog.objects.filter(action="Returned stock").count(), 1)

    def test_t7_a_collected_reservation_never_expires(self):
        reservation = self.reserve()
        collect_reservation(self.karim, reservation, "cash")
        reserved_minutes_ago(reservation, 121)
        expire_reservations()
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "collected")
        self.assertEqual(self.quantity_of(self.syrup_batch), 8)


class CollectReservationTests(ReservationTestData):
    def test_10_3_collecting_records_a_reservation_sale(self):
        reservation = self.reserve()
        sale = collect_reservation(self.karim, reservation, "card")
        self.assertEqual(
            (sale.staff, sale.payment_method, sale.source, sale.patient),
            (self.karim, "card", "reservation", self.maya),
        )
        line = sale.saleitem_set.get()
        self.assertEqual((line.medicine, line.quantity, line.unit_price), (self.syrup, 2, Decimal("4.50")))

    def test_10_3_collecting_marks_the_reservation_collected(self):
        reservation = self.reserve()
        collect_reservation(self.karim, reservation, "cash")
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "collected")

    def test_10_3_collecting_does_not_touch_the_stock_again(self):
        reservation = self.reserve()
        collect_reservation(self.karim, reservation, "cash")
        self.assertEqual(self.quantity_of(self.syrup_batch), 8)

    def test_10_3_an_expired_reservation_cannot_be_collected(self):
        reservation = reserved_minutes_ago(self.reserve(), 121)
        expire_reservations()
        reservation.refresh_from_db()
        with self.assertRaises(ValidationError):
            collect_reservation(self.karim, reservation, "cash")
        self.assertFalse(Sale.objects.exists())

    def test_10_3_a_reservation_cannot_be_collected_twice(self):
        reservation = self.reserve()
        collect_reservation(self.karim, reservation, "cash")
        with self.assertRaises(ValidationError):
            collect_reservation(self.karim, reservation, "cash")
        self.assertEqual(Sale.objects.count(), 1)


class ReservationRowsTests(ReservationTestData):
    def test_10_3_active_reservations_come_first_the_soonest_to_expire_on_top(self):
        newer = reserved_minutes_ago(self.reserve(), 10)
        older = reserved_minutes_ago(self.reserve(self.samira, self.cetirizine, 1), 90)
        ended = reserved_minutes_ago(self.reserve(), 130)
        expire_reservations()
        self.assertEqual([row["reservation"] for row in reservation_rows()], [older, newer, ended])

    def test_10_3_active_rows_say_how_long_is_left(self):
        reserved_minutes_ago(self.reserve(), 28)
        self.assertEqual(reservation_rows()[0]["time_left"], "1 h 32 min left")

    def test_10_3_rows_under_30_minutes_are_marked_soon(self):
        reserved_minutes_ago(self.reserve(), 100)
        reserved_minutes_ago(self.reserve(self.samira, self.cetirizine, 1), 80)
        self.assertEqual([row["soon"] for row in reservation_rows()], [True, False])

    def test_10_3_ended_rows_have_no_time_left(self):
        reserved_minutes_ago(self.reserve(), 130)
        expire_reservations()
        row = reservation_rows()[0]
        self.assertIsNone(row["time_left"])
        self.assertFalse(row["soon"])

    def test_10_3_reservations_that_ended_more_than_a_day_ago_drop_off(self):
        reservation = self.reserve()
        collect_reservation(self.karim, reservation, "cash")
        reserved_minutes_ago(reservation, 25 * 60)
        self.assertEqual(reservation_rows(), [])

    def test_10_3_rows_can_be_searched_by_patient_name_or_phone(self):
        maya_reservation = self.reserve()
        samira_reservation = self.reserve(self.samira, self.cetirizine, 1)
        self.assertEqual([row["reservation"] for row in reservation_rows("sam")], [samira_reservation])
        self.assertEqual([row["reservation"] for row in reservation_rows("100 002")], [maya_reservation])

    def test_10_3_the_count_only_counts_active_reservations(self):
        self.reserve()
        collect_reservation(self.karim, self.reserve(), "cash")
        self.assertEqual(active_reservation_count(), 1)


class ReservationPagesTestData(ReservationTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rana = get_user_model().objects.create(username="rana", role="pharmacist")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.karim)
        self.maya_reservation = reserved_minutes_ago(self.reserve(), 28)
        self.samira_reservation = reserved_minutes_ago(self.reserve(self.samira, self.cetirizine, 1), 100)


class ReservationsTabTests(ReservationPagesTestData):
    def test_10_3_the_tab_counts_active_reservations(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, 'href="/prescriptions/?show=reservations&amp;search="')
        self.assertContains(response, "Reservations 2")

    def test_10_3_the_tab_sits_between_prescriptions_and_not_collected(self):
        page = self.client.get("/prescriptions/").content.decode()
        self.assertLess(page.index("Prescriptions 0"), page.index("Reservations 2"))
        self.assertLess(page.index("Reservations 2"), page.index("Not collected 0"))

    def test_10_3_the_tab_lists_reservations_with_the_time_left(self):
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, 'class="selected">Reservations 2')
        self.assertContains(response, "Paracetamol syrup 500 mg · 2 units")
        self.assertContains(response, "Cetirizine 500 mg · 1 unit")
        self.assertContains(response, "1 h 32 min left")
        self.assertContains(response, "20 min left")

    def test_10_3_a_reservation_ending_soon_is_marked(self):
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, "reservation-time soon")

    def test_10_3_active_reservations_can_be_collected(self):
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, f'href="/reservations/{self.maya_reservation.pk}/collect/"')

    def test_10_3_an_expired_reservation_says_its_stock_is_back(self):
        old = reserved_minutes_ago(self.reserve(), 150)
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, "Expired · back in stock")
        self.assertNotContains(response, f'href="/reservations/{old.pk}/collect/"')

    def test_10_3_the_search_works_on_the_reservations_tab(self):
        response = self.client.get("/prescriptions/", {"show": "reservations", "search": "sam"})
        self.assertEqual(response.content.decode().count('class="reservation-row"'), 1)
        self.assertContains(response, '<input type="hidden" name="show" value="reservations">', html=True)

    def test_10_3_an_empty_tab_says_so(self):
        Reservation.objects.all().delete()
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, "No reservations right now.")

    def test_10_3_the_side_panel_shows_reservations_on_every_tab(self):
        for show in ["all", "ready", "reservations"]:
            response = self.client.get("/prescriptions/", {"show": show})
            self.assertContains(response, "Held for 2 hours, then back to stock")
            self.assertContains(response, "2 active")
            self.assertContains(response, 'class="panel-reservation"', count=2)

    def test_10_3_the_side_panel_shows_four_reservations_at_most(self):
        for count in range(4):
            self.reserve(quantity=1)
        response = self.client.get("/prescriptions/")
        self.assertContains(response, 'class="panel-reservation"', count=4)
        self.assertContains(response, "6 active")


class ExpiryOnPageLoadTests(ReservationPagesTestData):
    def test_t7_opening_a_page_expires_old_reservations(self):
        for page in ["/prescriptions/", "/dashboard/", "/inventory/", "/sales/"]:
            old = reserved_minutes_ago(self.reserve(quantity=1), 121)
            self.client.get(page)
            old.refresh_from_db()
            self.assertEqual(old.status, "expired", page)

    def test_t7_the_stock_is_back_before_the_page_shows_it(self):
        reserved_minutes_ago(self.reserve(quantity=5), 121)
        response = self.client.get("/sales/", {"search": "syrup"})
        self.assertContains(response, "8 in stock")


class CollectReservationPageTests(ReservationPagesTestData):
    def collect_address(self, reservation):
        return f"/reservations/{reservation.pk}/collect/"

    def test_10_3_collect_opens_a_popup_over_the_reservations(self):
        response = self.client.get(self.collect_address(self.maya_reservation))
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "Collect Reservation")
        self.assertContains(response, 'name="payment_method"')
        self.assertContains(response, "Paracetamol syrup 500 mg · 2 units")
        self.assertContains(response, 'class="selected">Reservations 2')

    def test_10_3_collecting_records_the_sale_and_goes_back_to_the_tab(self):
        response = self.client.post(self.collect_address(self.maya_reservation), {"payment_method": "cash"}, follow=True)
        self.assertRedirects(response, "/prescriptions/?show=reservations")
        sale = Sale.objects.get()
        self.assertEqual((sale.source, sale.patient, sale.staff), ("reservation", self.maya, self.karim))
        self.assertContains(response, f"Reservation collected: Sale {sale.pk}, $9.00, paid by cash.")
        self.assertContains(response, "Reservations 1")

    def test_10_3_a_payment_method_is_required(self):
        response = self.client.post(self.collect_address(self.maya_reservation), {})
        self.assertContains(response, "<dialog open")
        self.maya_reservation.refresh_from_db()
        self.assertEqual(self.maya_reservation.status, "active")
        self.assertFalse(Sale.objects.exists())

    def test_10_3_a_reservation_past_its_2_hours_cannot_be_collected(self):
        old = reserved_minutes_ago(self.reserve(), 121)
        response = self.client.post(self.collect_address(old), {"payment_method": "cash"}, follow=True)
        self.assertContains(response, "Only an active reservation can be collected.")
        old.refresh_from_db()
        self.assertEqual(old.status, "expired")
        self.assertFalse(Sale.objects.exists())

    def test_10_3_an_unknown_reservation_is_not_found(self):
        self.assertEqual(self.client.get("/reservations/9999/collect/").status_code, 404)

    def test_10_3_every_staff_role_can_collect(self):
        self.client.force_login(self.rana)
        self.assertEqual(self.client.get(self.collect_address(self.maya_reservation)).status_code, 200)

    def test_10_3_signed_out_visitors_go_to_the_staff_login(self):
        self.client.logout()
        address = self.collect_address(self.maya_reservation)
        self.assertRedirects(self.client.get(address), f"/login/staff/?next={address}")

    def test_10_3_patient_side_users_are_refused(self):
        self.client.force_login(self.maya_user)
        address = self.collect_address(self.maya_reservation)
        self.assertEqual(self.client.get(address).status_code, 403)
        self.assertEqual(self.client.post(address, {"payment_method": "cash"}).status_code, 403)


def collected_item(patient, medicine, quantity=30, days_of_supply=30):
    prescription = Prescription.objects.create(patient=patient, status="collected", collected_at=timezone.now())
    return PrescriptionItem.objects.create(
        prescription=prescription,
        medicine=medicine,
        quantity=quantity,
        days_of_supply=days_of_supply,
        long_term=True,
    )


def requested_minutes_ago(refill_request, minutes):
    RefillRequest.objects.filter(pk=refill_request.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))
    refill_request.refresh_from_db()
    return refill_request


def delay(refill_request):
    RefillRequest.objects.filter(pk=refill_request.pk).update(status="delayed")
    refill_request.refresh_from_db()
    return refill_request


class RefillTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.rana = User.objects.create(username="rana", role="pharmacist")
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.father_user = User.objects.create(username="omars_father", role="patient")
        cls.father = Patient.objects.create(
            name="Omar's Father",
            date_of_birth=date(1949, 11, 5),
            phone="03 100 003",
            user=cls.father_user,
        )
        cls.samira_user = User.objects.create(username="samira", role="patient")
        cls.samira = Patient.objects.create(
            name="Samira",
            date_of_birth=date(1958, 3, 14),
            phone="03 100 001",
            user=cls.samira_user,
        )
        cls.losartan = make_medicine("Losartan", "1000000000010", "10.00")
        cls.metformin = make_medicine("Metformin", "1000000000006", "7.50")

    def setUp(self):
        self.losartan_batch = make_batch(self.losartan, "LOS-001", 100)
        self.losartan_item = collected_item(self.father, self.losartan)
        self.metformin_item = collected_item(self.father, self.metformin, quantity=60)

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity


class RequestRefillTests(RefillTestData):
    def test_11_2_a_new_refill_request_is_waiting(self):
        refill_request = request_refill(self.father_user, self.losartan_item)
        self.assertEqual(refill_request.status, "waiting")
        self.assertEqual(refill_request.get_status_display(), "New")
        self.assertEqual((refill_request.prescription_item, refill_request.requested_by), (self.losartan_item, self.father_user))
        self.assertIsNone(refill_request.new_prescription)

    def test_11_2_an_item_with_an_open_request_cannot_be_requested_again(self):
        request_refill(self.father_user, self.losartan_item)
        with self.assertRaises(ValidationError):
            request_refill(self.father_user, self.losartan_item)
        self.assertEqual(RefillRequest.objects.count(), 1)

    def test_11_2_a_delayed_request_is_still_open(self):
        delay(request_refill(self.father_user, self.losartan_item))
        with self.assertRaises(ValidationError):
            request_refill(self.father_user, self.losartan_item)

    def test_11_2_after_an_approval_the_item_can_be_requested_again(self):
        approve_refill(request_refill(self.father_user, self.losartan_item))
        request_refill(self.father_user, self.losartan_item)
        self.assertEqual(RefillRequest.objects.filter(status="waiting").count(), 1)


class ApproveRefillTests(RefillTestData):
    def test_11_2_approving_creates_a_received_refill_prescription(self):
        prescription = approve_refill(request_refill(self.father_user, self.losartan_item))
        self.assertEqual((prescription.patient, prescription.status), (self.father, "received"))
        self.assertEqual(prescription.refill_of, self.losartan_item)

    def test_11_2_the_refill_has_the_same_medicine_quantity_and_supply(self):
        prescription = approve_refill(request_refill(self.father_user, self.metformin_item))
        item = prescription.prescriptionitem_set.get()
        self.assertEqual(
            (item.medicine, item.quantity, item.days_of_supply, item.long_term),
            (self.metformin, 60, 30, True),
        )
        self.assertIsNone(item.batch)

    def test_11_2_approving_marks_the_request_approved_and_links_the_prescription(self):
        refill_request = request_refill(self.father_user, self.losartan_item)
        prescription = approve_refill(refill_request)
        refill_request.refresh_from_db()
        self.assertEqual(refill_request.status, "approved")
        self.assertEqual(refill_request.new_prescription, prescription)

    def test_11_2_approving_takes_no_stock(self):
        approve_refill(request_refill(self.father_user, self.losartan_item))
        self.assertEqual(self.quantity_of(self.losartan_batch), 100)

    def test_11_2_a_delayed_request_can_be_approved(self):
        refill_request = delay(request_refill(self.father_user, self.losartan_item))
        approve_refill(refill_request)
        refill_request.refresh_from_db()
        self.assertEqual(refill_request.status, "approved")

    def test_11_2_an_approved_request_cannot_be_approved_again(self):
        refill_request = request_refill(self.father_user, self.losartan_item)
        approve_refill(refill_request)
        with self.assertRaises(ValidationError):
            approve_refill(refill_request)
        self.assertEqual(Prescription.objects.filter(refill_of=self.losartan_item).count(), 1)


class RefillRowsTests(RefillTestData):
    def test_11_2_open_requests_are_listed_oldest_first(self):
        newer = requested_minutes_ago(request_refill(self.father_user, self.losartan_item), 10)
        older = requested_minutes_ago(request_refill(self.father_user, self.metformin_item), 600)
        self.assertEqual([row["request"] for row in refill_rows()], [older, newer])

    def test_11_2_delayed_requests_stay_and_approved_ones_leave(self):
        delayed = delay(request_refill(self.father_user, self.losartan_item))
        approve_refill(request_refill(self.father_user, self.metformin_item))
        self.assertEqual([row["request"] for row in refill_rows()], [delayed])

    def test_11_2_rows_can_be_searched_by_patient_name_or_phone(self):
        father_request = request_refill(self.father_user, self.losartan_item)
        samira_item = collected_item(self.samira, self.losartan)
        samira_request = request_refill(self.samira_user, samira_item)
        self.assertEqual([row["request"] for row in refill_rows("sam")], [samira_request])
        self.assertEqual([row["request"] for row in refill_rows("100 003")], [father_request])

    def test_11_2_the_tab_counts_open_requests_and_the_panel_counts_new_ones(self):
        request_refill(self.father_user, self.losartan_item)
        delay(request_refill(self.father_user, self.metformin_item))
        self.assertEqual(open_refill_count(), 2)
        self.assertEqual(new_refill_count(), 1)

    def test_11_3_a_request_for_a_medicine_with_no_stock_is_flagged(self):
        request_refill(self.father_user, self.metformin_item)
        self.assertTrue(refill_rows()[0]["out_of_stock"])

    def test_11_3_a_request_for_a_medicine_in_stock_is_not_flagged(self):
        request_refill(self.father_user, self.losartan_item)
        self.assertFalse(refill_rows()[0]["out_of_stock"])

    def test_11_3_expired_stock_does_not_count(self):
        make_batch(self.metformin, "MET-OLD", 50, days_until_expiry=-1)
        request_refill(self.father_user, self.metformin_item)
        self.assertTrue(refill_rows()[0]["out_of_stock"])

    def test_11_3_any_available_stock_clears_the_flag(self):
        make_batch(self.metformin, "MET-001", 5)
        request_refill(self.father_user, self.metformin_item)
        self.assertFalse(refill_rows()[0]["out_of_stock"])


class RefillPagesTestData(RefillTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nadim = get_user_model().objects.create(username="nadim", role="owner", is_pharmacist=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.rana)
        self.metformin_request = requested_minutes_ago(request_refill(self.father_user, self.metformin_item), 600)
        self.losartan_request = requested_minutes_ago(request_refill(self.father_user, self.losartan_item), 30)

    def approve_address(self, refill_request):
        return f"/reservations/refills/{refill_request.pk}/approve/"


class RefillRequestsTabTests(RefillPagesTestData):
    def test_11_2_the_tab_counts_open_requests(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, 'href="/prescriptions/?show=refills&amp;search="')
        self.assertContains(response, "Refill requests 2")

    def test_11_2_the_tab_sits_between_reservations_and_not_collected(self):
        page = self.client.get("/prescriptions/").content.decode()
        self.assertLess(page.index("Reservations 0"), page.index("Refill requests 2"))
        self.assertLess(page.index("Refill requests 2"), page.index("Not collected 0"))

    def test_11_2_the_tab_lists_requests_oldest_first(self):
        page = self.client.get("/prescriptions/", {"show": "refills"}).content.decode()
        self.assertIn('class="selected">Refill requests 2', page)
        self.assertLess(page.index("Metformin 500 mg · 60 units"), page.index("Losartan 500 mg · 30 units"))
        self.assertEqual(page.count('class="refill-row"'), 2)

    def test_11_2_new_and_delayed_requests_are_labelled(self):
        delay(self.losartan_request)
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, '<span class="pill refill-status waiting">New</span>', html=True)
        self.assertContains(response, '<span class="pill refill-status delayed">Delayed</span>', html=True)

    def test_11_3_an_out_of_stock_request_is_flagged(self):
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, '<small class="refill-flag">Out of stock</small>', count=1, html=True)

    def test_11_2_a_pharmacist_can_approve(self):
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, f'action="{self.approve_address(self.losartan_request)}"')

    def test_11_2_an_assistant_sees_no_approve_button(self):
        self.client.force_login(self.karim)
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, "Losartan 500 mg · 30 units")
        self.assertNotContains(response, "/approve/")

    def test_11_2_the_search_works_on_the_refills_tab(self):
        samira_item = collected_item(self.samira, self.losartan)
        request_refill(self.samira_user, samira_item)
        response = self.client.get("/prescriptions/", {"show": "refills", "search": "sam"})
        self.assertEqual(response.content.decode().count('class="refill-row"'), 1)
        self.assertContains(response, '<input type="hidden" name="show" value="refills">', html=True)

    def test_11_2_an_empty_tab_says_so(self):
        RefillRequest.objects.all().delete()
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, "No refill requests right now.")


class RefillPanelTests(RefillPagesTestData):
    def test_11_2_the_side_panel_has_a_refill_requests_card(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, "Refill Requests")
        self.assertContains(response, "Sent by patients from the chat")
        self.assertContains(response, "2 new")
        self.assertContains(response, 'href="/prescriptions/?show=refills"')
        self.assertContains(response, "View all refill requests")

    def test_11_2_the_card_shows_three_requests_at_most(self):
        for name, barcode in [("Aspirin", "1000000000014"), ("Omeprazole", "1000000000008")]:
            request_refill(self.father_user, collected_item(self.father, make_medicine(name, barcode, "3.00")))
        self.assertContains(self.client.get("/prescriptions/"), 'class="panel-refill"', count=3)

    def test_11_3_the_card_says_when_a_medicine_is_out_of_stock(self):
        response = self.client.get("/prescriptions/")
        self.assertContains(response, "Metformin 500 mg · out of stock")
        self.assertContains(response, "Losartan 500 mg · requested")


class ApproveRefillPageTests(RefillPagesTestData):
    def test_11_2_approving_opens_the_new_refill_prescription(self):
        response = self.client.post(self.approve_address(self.losartan_request), follow=True)
        prescription = Prescription.objects.get(refill_of=self.losartan_item)
        self.assertRedirects(response, f"/prescriptions/{prescription.pk}/")
        shown = [str(message) for message in response.context["messages"]]
        self.assertEqual(shown, [f"Refill approved: prescription {prescription.pk} created for Omar's Father."])

    def test_11_2_an_approved_request_leaves_the_tab(self):
        self.client.post(self.approve_address(self.losartan_request))
        self.assertContains(self.client.get("/prescriptions/"), "Refill requests 1")

    def test_11_2_an_owner_who_is_a_pharmacist_can_approve(self):
        self.client.force_login(self.nadim)
        self.client.post(self.approve_address(self.losartan_request))
        self.losartan_request.refresh_from_db()
        self.assertEqual(self.losartan_request.status, "approved")

    def test_11_2_approving_twice_is_refused(self):
        self.client.post(self.approve_address(self.losartan_request))
        response = self.client.post(self.approve_address(self.losartan_request), follow=True)
        self.assertRedirects(response, "/prescriptions/?show=refills")
        self.assertContains(response, "Only a new or delayed refill request can be approved.")
        self.assertEqual(Prescription.objects.filter(refill_of=self.losartan_item).count(), 1)

    def test_11_2_opening_the_approve_address_changes_nothing(self):
        self.assertRedirects(self.client.get(self.approve_address(self.losartan_request)), "/prescriptions/?show=refills")
        self.losartan_request.refresh_from_db()
        self.assertEqual(self.losartan_request.status, "waiting")

    def test_11_2_an_assistant_cannot_approve(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.client.post(self.approve_address(self.losartan_request)).status_code, 403)
        self.assertFalse(Prescription.objects.filter(refill_of__isnull=False).exists())

    def test_11_2_patient_side_users_cannot_approve(self):
        self.client.force_login(self.father_user)
        self.assertEqual(self.client.post(self.approve_address(self.losartan_request)).status_code, 403)

    def test_11_2_an_unknown_request_is_not_found(self):
        self.assertEqual(self.client.post("/reservations/refills/9999/approve/").status_code, 404)


def next_week():
    return timezone.localdate() + timedelta(days=7)


class DelayRefillTests(RefillTestData):
    def test_11_4_delaying_marks_the_request_delayed_with_its_date(self):
        refill_request = request_refill(self.father_user, self.metformin_item)
        delay_refill(refill_request, next_week())
        refill_request.refresh_from_db()
        self.assertEqual((refill_request.status, refill_request.expected_on), ("delayed", next_week()))

    def test_11_4_the_patient_is_told_the_medicine_and_the_date(self):
        delay_refill(request_refill(self.father_user, self.metformin_item), next_week())
        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.father_user)
        self.assertEqual(
            notification.text,
            f"Omar's Father's refill of Metformin 500 mg is delayed, expected {next_week():%d %b %Y}.",
        )
        self.assertEqual(notification.link, "/")

    def test_11_4_the_expected_date_must_be_after_today(self):
        refill_request = request_refill(self.father_user, self.metformin_item)
        with self.assertRaises(ValidationError):
            delay_refill(refill_request, timezone.localdate())
        refill_request.refresh_from_db()
        self.assertEqual(refill_request.status, "waiting")
        self.assertFalse(Notification.objects.exists())

    def test_11_4_a_delayed_request_cannot_be_delayed_again(self):
        refill_request = request_refill(self.father_user, self.metformin_item)
        delay_refill(refill_request, next_week())
        with self.assertRaises(ValidationError):
            delay_refill(refill_request, next_week() + timedelta(days=7))
        refill_request.refresh_from_db()
        self.assertEqual(refill_request.expected_on, next_week())
        self.assertEqual(Notification.objects.count(), 1)

    def test_11_4_an_approved_request_cannot_be_delayed(self):
        refill_request = request_refill(self.father_user, self.metformin_item)
        approve_refill(refill_request)
        with self.assertRaises(ValidationError):
            delay_refill(refill_request, next_week())

    def test_11_4_a_delayed_request_can_still_be_approved(self):
        refill_request = request_refill(self.father_user, self.metformin_item)
        delay_refill(refill_request, next_week())
        approve_refill(refill_request)
        refill_request.refresh_from_db()
        self.assertEqual(refill_request.status, "approved")


class CaregiverRefillTests(RefillTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.omar = get_user_model().objects.create(username="omar", role="patient", first_name="Omar")

    def setUp(self):
        super().setUp()
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)

    def test_12_2_the_caregiver_is_told_about_a_delay_too(self):
        delay_refill(request_refill(self.omar, self.metformin_item), next_week())
        self.assertEqual(set(Notification.objects.values_list("user__username", flat=True)), {"omars_father", "omar"})
        self.assertEqual(
            Notification.objects.get(user=self.omar, text__contains="is delayed").text,
            f"Omar's Father's refill of Metformin 500 mg is delayed, expected {next_week():%d %b %Y}.",
        )
        self.assertEqual(Notification.objects.filter(user=self.omar, text__startswith="Refill requested").count(), 1)

    def test_12_2_each_row_says_which_caregiver_asked(self):
        request_refill(self.omar, self.metformin_item)
        request_refill(self.father_user, self.losartan_item)
        self.assertEqual([row["requested_by_caregiver"] for row in refill_rows()], ["Omar", ""])

    def test_12_2_the_refill_requests_tab_shows_the_caregiver(self):
        request_refill(self.omar, self.metformin_item)
        request_refill(self.father_user, self.losartan_item)
        self.client.force_login(self.rana)
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, '<small class="refill-requested-by">By caregiver Omar</small>', html=True)
        self.assertContains(response, "By caregiver", count=1)


class DelayRefillPageTests(RefillPagesTestData):
    def delay_address(self, refill_request):
        return f"/reservations/refills/{refill_request.pk}/delay/"

    def delay(self, refill_request, expected_on, follow=False):
        return self.client.post(self.delay_address(refill_request), {"expected_on": expected_on}, follow=follow)

    def shown_messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_11_4_new_requests_have_a_delay_button_for_pharmacists(self):
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, f'href="{self.delay_address(self.losartan_request)}"')
        self.assertContains(response, f'href="{self.delay_address(self.metformin_request)}"')

    def test_11_4_a_delayed_request_has_no_delay_button_but_can_be_approved(self):
        delay_refill(self.metformin_request, next_week())
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertNotContains(response, self.delay_address(self.metformin_request))
        self.assertContains(response, f'action="{self.approve_address(self.metformin_request)}"')

    def test_11_4_an_assistant_sees_no_delay_button(self):
        self.client.force_login(self.karim)
        self.assertNotContains(self.client.get("/prescriptions/", {"show": "refills"}), "/delay/")

    def test_11_4_delay_opens_a_popup_over_the_refill_requests(self):
        response = self.client.get(self.delay_address(self.losartan_request))
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "Delay Refill")
        self.assertContains(response, 'name="expected_on"')
        self.assertContains(response, "Losartan 500 mg · 30 units")
        self.assertContains(response, 'class="selected">Refill requests 2')

    def test_11_4_delaying_goes_back_to_the_tab_and_tells_the_patient(self):
        response = self.delay(self.losartan_request, next_week(), follow=True)
        self.assertRedirects(response, "/prescriptions/?show=refills")
        self.assertEqual(
            self.shown_messages(response),
            [f"Refill delayed until {next_week():%d %b %Y}. Omar's Father was told."],
        )
        self.losartan_request.refresh_from_db()
        self.assertEqual(self.losartan_request.status, "delayed")
        self.assertEqual(Notification.objects.get().user, self.father_user)

    def test_11_4_a_delayed_row_shows_its_expected_date(self):
        self.delay(self.losartan_request, next_week())
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, '<span class="pill refill-status delayed">Delayed</span>', html=True)
        self.assertContains(response, f"Expected {next_week():%d %b %Y}")

    def test_11_4_a_date_that_is_not_after_today_keeps_the_popup_open(self):
        response = self.delay(self.losartan_request, timezone.localdate())
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "The expected date must be after today.")
        self.losartan_request.refresh_from_db()
        self.assertEqual(self.losartan_request.status, "waiting")

    def test_11_4_a_date_is_required(self):
        response = self.client.post(self.delay_address(self.losartan_request), {})
        self.assertContains(response, "<dialog open")
        self.assertFalse(Notification.objects.exists())

    def test_11_4_a_patient_with_no_login_is_mentioned(self):
        ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))
        refill_request = request_refill(self.rana, collected_item(ahmad, self.losartan))
        response = self.delay(refill_request, next_week(), follow=True)
        self.assertEqual(
            self.shown_messages(response),
            [f"Refill delayed until {next_week():%d %b %Y}. Ahmad has no login, so let them know by phone."],
        )
        self.assertFalse(Notification.objects.exists())

    def test_11_4_delaying_twice_is_refused(self):
        delay_refill(self.losartan_request, next_week())
        response = self.delay(self.losartan_request, next_week() + timedelta(days=7), follow=True)
        self.assertRedirects(response, "/prescriptions/?show=refills")
        self.assertEqual(self.shown_messages(response), ["Only a new refill request can be delayed."])
        self.assertEqual(Notification.objects.count(), 1)

    def test_11_4_an_owner_who_is_a_pharmacist_can_delay(self):
        self.client.force_login(self.nadim)
        self.delay(self.losartan_request, next_week())
        self.losartan_request.refresh_from_db()
        self.assertEqual(self.losartan_request.status, "delayed")

    def test_11_4_an_assistant_cannot_delay(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.client.get(self.delay_address(self.losartan_request)).status_code, 403)
        self.assertEqual(self.delay(self.losartan_request, next_week()).status_code, 403)

    def test_11_4_patient_side_users_cannot_delay(self):
        self.client.force_login(self.father_user)
        self.assertEqual(self.delay(self.losartan_request, next_week()).status_code, 403)

    def test_11_4_an_unknown_request_is_not_found(self):
        self.assertEqual(self.client.get("/reservations/refills/9999/delay/").status_code, 404)

class FindMedicineTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.maya_user = User.objects.create(username="maya", role="patient")
        cls.maya = Patient.objects.create(name="Maya", date_of_birth=date(1988, 9, 21), user=cls.maya_user)
        cls.omar = User.objects.create(username="omar", role="patient")
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.syrup = make_medicine("Paracetamol syrup", "1000000000017", "4.50")
        cls.tablets = make_medicine("Paracetamol", "1000000000005", "3.00")
        cls.cetirizine = make_medicine("Cetirizine", "1000000000011", "5.00")

    def setUp(self):
        self.syrup_batch = make_batch(self.syrup, "SYR-001", 34)
        make_batch(self.cetirizine, "CET-001", 2)

    def found(self, name, quantity=1):
        return {row["medicine"]: row for row in medicine_availability(name, quantity)}


class MedicineAvailabilityTests(FindMedicineTestData):
    def test_10_1_enough_stock_is_available(self):
        row = self.found("syrup")[self.syrup]
        self.assertEqual((row["available"], row["status"], row["label"]), (34, "available", "Available"))

    def test_10_1_less_than_asked_says_how_many_are_left(self):
        row = self.found("cetirizine", quantity=5)[self.cetirizine]
        self.assertEqual((row["available"], row["status"], row["label"]), (2, "low", "Only 2 left"))

    def test_10_1_exactly_enough_is_available(self):
        self.assertEqual(self.found("cetirizine", quantity=2)[self.cetirizine]["status"], "available")

    def test_10_1_no_stock_is_out_of_stock(self):
        row = self.found("paracetamol")[self.tablets]
        self.assertEqual((row["available"], row["status"], row["label"]), (0, "out", "Out of stock"))

    def test_10_1_expired_stock_does_not_count(self):
        make_batch(self.tablets, "PAR-OLD", 50, days_until_expiry=-1)
        self.assertEqual(self.found("paracetamol")[self.tablets]["status"], "out")

    def test_10_1_part_of_a_name_in_any_case_is_found(self):
        self.assertEqual(list(self.found("PARACET")), [self.tablets, self.syrup])

    def test_10_1_no_name_finds_nothing(self):
        self.assertEqual(medicine_availability("", 1), [])

    def test_10_1_at_most_five_medicines_are_shown(self):
        for number in range(1, 8):
            make_medicine(f"Vitamin {number}", f"20000000000{number:02d}", "2.00")
        self.assertEqual(len(medicine_availability("vitamin", 1)), 5)


class FindMedicinePageTests(FindMedicineTestData):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.maya_user)

    def results(self, name, quantity=1):
        return self.client.get("/reservations/find/results/", {"name": name, "quantity": quantity})

    def test_10_1_the_search_page_uses_the_figma_wording(self):
        response = self.client.get("/reservations/find/")
        for words in [
            "Medicine availability",
            "Find a Medicine",
            "Check availability before visiting your pharmacy",
            "Medicine Name",
            "Quantity",
            "Check Availability",
            "Real-Time Availability",
            "Save Time",
            "Reliable Information",
        ]:
            self.assertContains(response, words)

    def test_10_1_the_search_form_sends_a_name_and_a_quantity(self):
        response = self.client.get("/reservations/find/")
        self.assertContains(response, 'action="/reservations/find/results/"')
        self.assertContains(response, 'method="get"')
        self.assertContains(response, 'name="name"')
        self.assertContains(response, '<option value="1" selected>1</option>', html=True)
        self.assertContains(response, '<option value="10">10</option>', html=True)

    def test_10_1_the_menu_links_to_find_medicine(self):
        response = self.client.get("/home/")
        self.assertContains(response, 'href="/reservations/find/"')
        response = self.client.get("/reservations/find/")
        self.assertContains(response, 'href="/reservations/find/" class="active"')

    def test_10_1_results_show_each_medicine_with_its_stock(self):
        response = self.results("syrup")
        self.assertContains(response, "Medicine Availability")
        self.assertContains(response, "Here are the results for your search")
        self.assertContains(response, "Paracetamol syrup 500 mg")
        self.assertContains(response, "Test class")
        self.assertContains(response, '<span class="availability-badge available">Available</span>', html=True)
        self.assertContains(response, "In stock: 34")

    def test_10_1_results_say_when_there_is_not_enough(self):
        response = self.results("cetirizine", quantity=5)
        self.assertContains(response, '<span class="availability-badge low">Only 2 left</span>', html=True)

    def test_10_1_results_say_when_it_is_out_of_stock(self):
        response = self.results("paracetamol")
        self.assertContains(response, '<span class="availability-badge out">Out of stock</span>', html=True)
        self.assertContains(response, "In stock: 0")

    def test_10_1_results_show_the_pharmacy_once(self):
        response = self.results("paracetamol")
        self.assertContains(response, settings.PHARMACY_NAME)
        self.assertContains(response, settings.PHARMACY_ADDRESS, count=1)
        self.assertContains(response, settings.PHARMACY_PHONE)
        self.assertContains(response, settings.PHARMACY_HOURS)

    def test_10_1_results_link_back_to_the_search(self):
        response = self.results("syrup")
        self.assertContains(response, "Back to Search")
        self.assertContains(response, 'href="/reservations/find/"')

    def test_10_1_a_name_with_no_match_says_so(self):
        response = self.results("zzz")
        self.assertContains(response, 'No medicine matches "zzz".')

    def test_10_1_a_name_is_required(self):
        response = self.results("")
        self.assertContains(response, "Enter a medicine name.")
        self.assertContains(response, "Find a Medicine")

    def test_10_1_the_quantity_must_be_1_to_10(self):
        for quantity in [0, 11, "two"]:
            response = self.results("syrup", quantity)
            self.assertContains(response, "Find a Medicine")
            self.assertNotContains(response, "Here are the results for your search")

    def test_t7_a_stock_lookup_expires_old_reservations_first(self):
        reserved_minutes_ago(reserve_medicine(self.maya_user, self.maya, self.syrup, 4), 121)
        self.assertContains(self.results("syrup"), "In stock: 34")

    def test_10_1_the_bell_shows_unread_notifications_on_both_pages(self):
        notify(self.maya_user, "Your prescription is ready", "/")
        for response in [self.client.get("/reservations/find/"), self.results("syrup")]:
            self.assertContains(response, 'class="patient-bell unread"')

    def test_10_1_a_caregiver_with_no_patient_record_can_search(self):
        self.client.force_login(self.omar)
        self.assertContains(self.results("syrup"), "In stock: 34")

    def test_10_1_staff_cannot_open_the_patient_pages(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.client.get("/reservations/find/").status_code, 403)
        self.assertEqual(self.results("syrup").status_code, 403)

    def test_10_1_signed_out_visitors_go_to_the_patient_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/reservations/find/"), "/login/?next=/reservations/find/")

class ReserveTestData(FindMedicineTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.samira_user = User.objects.create(username="samira", role="patient")
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14), user=cls.samira_user)


class PatientReservationTests(ReserveTestData):
    def test_10_2_the_pickup_deadline_is_2_hours_after_reserving(self):
        reservation = reserve_medicine(self.maya_user, self.maya, self.syrup, 1)
        self.assertEqual(pickup_deadline(reservation), reservation.created_at + timedelta(hours=2))

    def test_10_2_a_patient_finds_their_own_reservation(self):
        reservation = reserve_medicine(self.maya_user, self.maya, self.syrup, 1)
        self.assertEqual(patient_reservation(self.maya, reservation.pk), reservation)

    def test_10_2_another_patients_reservation_is_not_found(self):
        reservation = reserve_medicine(self.maya_user, self.maya, self.syrup, 1)
        with self.assertRaises(Http404):
            patient_reservation(self.samira, reservation.pk)


class ReserveMedicinePageTests(ReserveTestData):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.maya_user)

    def reserve(self, medicine, quantity=2, follow=False):
        return self.client.post("/reservations/reserve/", {"medicine": medicine.pk, "quantity": quantity}, follow=follow)

    def results(self, name, quantity=1):
        return self.client.get("/reservations/find/results/", {"name": name, "quantity": quantity})

    def shown_messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_10_2_an_available_medicine_can_be_reserved_from_the_results(self):
        response = self.results("syrup", 2)
        self.assertContains(response, 'action="/reservations/reserve/"')
        self.assertContains(response, f'<input type="hidden" name="medicine" value="{self.syrup.pk}">', html=True)
        self.assertContains(response, '<input type="hidden" name="quantity" value="2">', html=True)
        self.assertContains(response, "Reserve Medicine")

    def test_10_2_a_medicine_that_is_not_available_cannot_be_reserved(self):
        response = self.results("cetirizine", 5)
        self.assertNotContains(response, 'action="/reservations/reserve/"')
        response = self.results("paracetamol", 1)
        self.assertContains(response, 'action="/reservations/reserve/"', count=1)

    def test_10_2_a_caregiver_with_no_patient_record_sees_no_reserve_button(self):
        self.client.force_login(self.omar)
        self.assertNotContains(self.results("syrup"), 'action="/reservations/reserve/"')

    def test_10_2_reserving_takes_the_stock_and_opens_the_reservation(self):
        response = self.reserve(self.syrup, 2)
        reservation = Reservation.objects.get()
        self.assertRedirects(response, f"/reservations/{reservation.pk}/")
        self.assertEqual((reservation.patient, reservation.medicine, reservation.quantity), (self.maya, self.syrup, 2))
        self.assertEqual(reservation.status, "active")
        self.syrup_batch.refresh_from_db()
        self.assertEqual(self.syrup_batch.quantity, 32)

    def test_10_2_the_reservation_page_uses_the_figma_wording(self):
        response = self.reserve(self.syrup, 2, follow=True)
        reservation = Reservation.objects.get()
        deadline = timezone.localtime(reservation.created_at + timedelta(hours=2))
        for words in [
            "Medicine Reserved!",
            "Your medicine has been successfully reserved.",
            "Paracetamol syrup<small>500 mg</small>",
            "2 units",
            f"{settings.PHARMACY_NAME} Pharmacy",
            settings.PHARMACY_ADDRESS,
            f"Before {deadline:%H:%M}",
            "within 2 hours",
            "otherwise the reservation will expire.",
            "Return Home",
        ]:
            self.assertContains(response, words)
        self.assertContains(response, '<span class="reserved-status active">Active</span>', html=True)
        self.assertContains(response, 'href="/home/"')

    def test_10_2_the_pharmacy_sees_the_reservation(self):
        self.reserve(self.syrup, 2)
        self.client.force_login(self.karim)
        response = self.client.get("/prescriptions/", {"show": "reservations"})
        self.assertContains(response, "Maya")
        self.assertContains(response, "Paracetamol syrup 500 mg · 2 units")

    def test_10_2_more_than_one_batch_holds_is_refused_and_nothing_changes(self):
        second_batch = make_batch(self.cetirizine, "CET-002", 3)
        response = self.reserve(self.cetirizine, 4, follow=True)
        self.assertRedirects(response, "/reservations/find/results/?name=Cetirizine&quantity=4")
        self.assertEqual(
            self.shown_messages(response),
            ["Sorry, Cetirizine 500 mg cannot be reserved in that quantity right now. Please ask the pharmacy."],
        )
        self.assertFalse(Reservation.objects.exists())
        second_batch.refresh_from_db()
        self.assertEqual(second_batch.quantity, 3)

    def test_10_2_an_out_of_stock_medicine_is_refused(self):
        response = self.reserve(self.tablets, 1, follow=True)
        self.assertContains(response, "cannot be reserved in that quantity right now")
        self.assertFalse(Reservation.objects.exists())

    def test_10_2_a_caregiver_with_no_patient_record_cannot_reserve(self):
        self.client.force_login(self.omar)
        response = self.reserve(self.syrup, 1, follow=True)
        self.assertEqual(
            self.shown_messages(response),
            ["Reservations are made for your own patient record. Please ask the pharmacy to set it up."],
        )
        self.assertFalse(Reservation.objects.exists())

    def test_10_2_a_bad_medicine_or_quantity_reserves_nothing(self):
        self.client.post("/reservations/reserve/", {"medicine": 9999, "quantity": 1})
        self.client.post("/reservations/reserve/", {"medicine": self.syrup.pk, "quantity": 11})
        self.assertFalse(Reservation.objects.exists())

    def test_10_2_opening_the_reserve_address_reserves_nothing(self):
        self.assertRedirects(self.client.get("/reservations/reserve/"), "/reservations/find/")
        self.assertFalse(Reservation.objects.exists())

    def test_10_2_someone_elses_reservation_is_not_found(self):
        reservation = reserve_medicine(self.samira_user, self.samira, self.syrup, 1)
        self.assertEqual(self.client.get(f"/reservations/{reservation.pk}/").status_code, 404)

    def test_t7_a_reservation_past_its_2_hours_shows_as_expired(self):
        reservation = reserved_minutes_ago(reserve_medicine(self.maya_user, self.maya, self.syrup, 1), 121)
        response = self.client.get(f"/reservations/{reservation.pk}/")
        self.assertContains(response, '<span class="reserved-status expired">Expired</span>', html=True)
        self.assertNotContains(response, "otherwise the reservation will expire.")

    def test_10_2_a_collected_reservation_says_so(self):
        reservation = reserve_medicine(self.maya_user, self.maya, self.syrup, 1)
        collect_reservation(self.karim, reservation, "cash")
        response = self.client.get(f"/reservations/{reservation.pk}/")
        self.assertContains(response, '<span class="reserved-status collected">Collected</span>', html=True)

    def test_10_2_the_home_cards_lead_to_find_a_medicine(self):
        response = self.client.get("/home/")
        self.assertContains(response, '<a href="/reservations/find/" class="home-card find">')
        self.assertContains(response, '<a href="/reservations/find/" class="home-card reserve">')

    def test_10_2_staff_cannot_use_the_patient_pages(self):
        reservation = reserve_medicine(self.maya_user, self.maya, self.syrup, 1)
        self.client.force_login(self.karim)
        self.assertEqual(self.reserve(self.syrup, 1).status_code, 403)
        self.assertEqual(self.client.get(f"/reservations/{reservation.pk}/").status_code, 403)

    def test_10_2_signed_out_visitors_go_to_the_patient_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/reservations/1/"), "/login/?next=/reservations/1/")


def collected_days_ago(patient, medicine, days, days_of_supply=30, long_term=True):
    collected_on = timezone.localdate() - timedelta(days=days)
    prescription = Prescription.objects.create(
        patient=patient,
        status="collected",
        collected_at=timezone.make_aware(datetime.combine(collected_on, time(12, 0))),
    )
    return PrescriptionItem.objects.create(
        prescription=prescription,
        medicine=medicine,
        quantity=60,
        days_of_supply=days_of_supply,
        long_term=long_term,
    )


def reminder_items(patient):
    return [reminder["item"] for reminder in refill_reminders(patient)]


def short_date(day):
    return f"{day.day} {day:%b}"


class RefillReminderTests(RefillTestData):
    def test_11_1a_a_medicine_that_runs_out_in_3_days_is_due(self):
        item = collected_days_ago(self.samira, self.metformin, 27)
        reminders = refill_reminders(self.samira)
        self.assertEqual([reminder["item"] for reminder in reminders], [item])
        self.assertEqual(reminders[0]["days_left"], 3)
        self.assertEqual(reminders[0]["runs_out_on"], timezone.localdate() + timedelta(days=3))
        self.assertEqual(reminders[0]["text"], "Your Metformin 500 mg will run out in 3 days.")

    def test_11_1a_it_runs_out_on_the_collected_date_plus_the_days_of_supply(self):
        item = collected_days_ago(self.samira, self.metformin, 10, days_of_supply=45)
        self.assertEqual(runs_out_on(item), timezone.localdate() + timedelta(days=35))

    def test_11_1a_a_medicine_with_4_days_left_is_not_due_yet(self):
        collected_days_ago(self.samira, self.metformin, 26)
        self.assertEqual(refill_reminders(self.samira), [])

    def test_11_1a_an_overdue_medicine_is_still_due(self):
        item = collected_days_ago(self.samira, self.metformin, 40)
        self.assertEqual(reminder_items(self.samira), [item])
        self.assertEqual(refill_reminders(self.samira)[0]["days_left"], -10)

    def test_11_1a_a_short_term_medicine_gets_no_reminder(self):
        collected_days_ago(self.samira, self.metformin, 40, long_term=False)
        self.assertEqual(refill_reminders(self.samira), [])

    def test_11_1a_only_the_latest_collection_of_a_medicine_counts(self):
        collected_days_ago(self.samira, self.metformin, 60)
        collected_days_ago(self.samira, self.metformin, 5)
        self.assertEqual(refill_reminders(self.samira), [])

    def test_11_1a_a_prescription_not_collected_yet_gets_no_reminder(self):
        item = collected_days_ago(self.samira, self.metformin, 40)
        Prescription.objects.filter(pk=item.prescription_id).update(status="ready")
        self.assertEqual(refill_reminders(self.samira), [])

    def test_11_1a_an_open_request_hides_the_reminder(self):
        item = collected_days_ago(self.samira, self.metformin, 40)
        refill_request = request_refill(self.samira_user, item)
        self.assertEqual(refill_reminders(self.samira), [])
        delay(refill_request)
        self.assertEqual(refill_reminders(self.samira), [])

    def test_11_1a_an_approved_refill_on_its_way_hides_the_reminder(self):
        item = collected_days_ago(self.samira, self.metformin, 40)
        prescription = approve_refill(request_refill(self.samira_user, item))
        self.assertTrue(refill_in_progress(item))
        self.assertEqual(refill_reminders(self.samira), [])
        Prescription.objects.filter(pk=prescription.pk).update(status="returned")
        self.assertFalse(refill_in_progress(item))
        self.assertEqual(reminder_items(self.samira), [item])

    def test_11_1a_a_deleted_refill_prescription_brings_the_reminder_back(self):
        item = collected_days_ago(self.samira, self.metformin, 40)
        delete_prescription(approve_refill(request_refill(self.samira_user, item)))
        self.assertEqual(reminder_items(self.samira), [item])

    def test_11_1a_each_patient_sees_only_their_own_reminders(self):
        item = collected_days_ago(self.samira, self.losartan, 40)
        self.assertEqual(reminder_items(self.samira), [item])
        self.assertEqual(refill_reminders(self.father), [])

    def test_11_1a_the_soonest_to_run_out_comes_first(self):
        losartan = collected_days_ago(self.samira, self.losartan, 28)
        metformin = collected_days_ago(self.samira, self.metformin, 35)
        self.assertEqual(reminder_items(self.samira), [metformin, losartan])

    def test_11_1a_no_patient_record_means_no_reminders(self):
        self.assertEqual(refill_reminders(None), [])

    def test_11_1a_the_reminder_sentence(self):
        for days_left, text in [
            (3, "Your Metformin 500 mg will run out in 3 days."),
            (1, "Your Metformin 500 mg will run out tomorrow."),
            (0, "Your Metformin 500 mg runs out today."),
            (-1, "Your Metformin 500 mg ran out yesterday."),
            (-10, "Your Metformin 500 mg ran out 10 days ago."),
        ]:
            self.assertEqual(runs_out_text(self.metformin, days_left), text)

    def test_11_1b_a_patient_finds_their_own_long_term_item(self):
        item = collected_days_ago(self.samira, self.metformin, 27)
        self.assertEqual(patient_long_term_item(self.samira, item.pk), item)

    def test_11_1b_other_items_are_not_found(self):
        short_term = collected_days_ago(self.samira, self.losartan, 27, long_term=False)
        not_collected = collected_days_ago(self.samira, self.metformin, 27)
        Prescription.objects.filter(pk=not_collected.prescription_id).update(status="ready")
        for patient, item in [
            (self.samira, self.losartan_item),
            (self.samira, short_term),
            (self.samira, not_collected),
            (None, self.losartan_item),
        ]:
            with self.assertRaises(Http404):
                patient_long_term_item(patient, item.pk)

    def test_11_1b_the_patient_sees_their_open_requests_oldest_first(self):
        losartan = collected_days_ago(self.samira, self.losartan, 27)
        metformin = collected_days_ago(self.samira, self.metformin, 28)
        first = requested_minutes_ago(request_refill(self.samira_user, metformin), 60)
        second = request_refill(self.samira_user, losartan)
        request_refill(self.father_user, self.losartan_item)
        self.assertEqual(list(patient_refill_requests(self.samira)), [first, second])
        approve_refill(first)
        self.assertEqual(list(patient_refill_requests(self.samira)), [second])


class RefillReminderPageTests(RefillTestData):
    def setUp(self):
        super().setUp()
        self.due_item = collected_days_ago(self.samira, self.metformin, 27)
        self.client.force_login(self.samira_user)

    def request_address(self, item):
        return f"/reservations/refills/request/{item.pk}/"

    def shown_messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_11_1a_the_home_page_shows_the_reminder(self):
        response = self.client.get("/home/")
        runs_out = timezone.localdate() + timedelta(days=3)
        self.assertContains(response, 'id="refill-reminders"')
        self.assertContains(response, "Refill reminder")
        self.assertContains(response, "Your Metformin 500 mg will run out in 3 days.")
        self.assertContains(response, f"Runs out {short_date(runs_out)}")
        self.assertContains(response, f'action="{self.request_address(self.due_item)}"')
        self.assertContains(response, "Request Refill")

    def test_11_1a_with_nothing_due_the_box_says_when_reminders_come(self):
        self.client.force_login(self.father_user)
        response = self.client.get("/home/")
        self.assertContains(response, "We will remind you 3 days before a long-term medicine runs out.")
        self.assertNotContains(response, 'action="/reservations/refills/request/')

    def test_11_1a_the_request_a_refill_card_leads_to_the_reminders(self):
        self.assertContains(self.client.get("/home/"), '<a href="#refill-reminders" class="home-card refill">')

    def test_11_1b_one_click_sends_the_request_to_the_pharmacy(self):
        response = self.client.post(self.request_address(self.due_item), follow=True)
        self.assertRedirects(response, "/home/")
        refill_request = RefillRequest.objects.get()
        self.assertEqual(
            (refill_request.prescription_item, refill_request.requested_by, refill_request.status),
            (self.due_item, self.samira_user, "waiting"),
        )
        self.assertEqual(
            self.shown_messages(response),
            ["Refill requested: Metformin 500 mg. The pharmacy will let you know when it is ready."],
        )

    def test_11_1b_after_requesting_the_reminder_becomes_sent_to_pharmacist(self):
        self.client.post(self.request_address(self.due_item))
        response = self.client.get("/home/")
        self.assertNotContains(response, 'action="/reservations/refills/request/')
        self.assertContains(response, "Metformin 500 mg")
        self.assertContains(response, '<span class="refill-reminder-status waiting">Sent to pharmacist</span>', html=True)

    def test_11_1b_a_delayed_request_shows_its_expected_date(self):
        expected = timezone.localdate() + timedelta(days=7)
        delay_refill(request_refill(self.samira_user, self.due_item), expected)
        response = self.client.get("/home/")
        self.assertContains(response, '<span class="refill-reminder-status delayed">Delayed</span>', html=True)
        self.assertContains(response, f"expected {short_date(expected)}")

    def test_11_1b_a_second_click_does_not_send_a_second_request(self):
        self.client.post(self.request_address(self.due_item), follow=True)
        response = self.client.post(self.request_address(self.due_item), follow=True)
        self.assertEqual(self.shown_messages(response), ["A refill of Metformin 500 mg has already been requested."])
        self.assertEqual(RefillRequest.objects.count(), 1)

    def test_11_1b_the_pharmacy_sees_the_request(self):
        self.client.post(self.request_address(self.due_item))
        self.client.force_login(self.karim)
        response = self.client.get("/prescriptions/", {"show": "refills"})
        self.assertContains(response, "Samira")
        self.assertContains(response, "Metformin 500 mg · 60 units")

    def test_11_1b_someone_elses_medicine_cannot_be_requested(self):
        self.assertEqual(self.client.post(self.request_address(self.losartan_item)).status_code, 404)
        self.assertFalse(RefillRequest.objects.exists())

    def test_11_1b_a_caregiver_with_no_patient_record_cannot_request(self):
        omar = get_user_model().objects.create(username="omar", role="patient")
        self.client.force_login(omar)
        self.assertEqual(self.client.post(self.request_address(self.due_item)).status_code, 404)
        self.assertFalse(RefillRequest.objects.exists())

    def test_11_1b_opening_the_address_requests_nothing(self):
        self.assertRedirects(self.client.get(self.request_address(self.due_item)), "/home/")
        self.assertFalse(RefillRequest.objects.exists())

    def test_11_1b_staff_cannot_use_the_patient_button(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.client.post(self.request_address(self.due_item)).status_code, 403)
        self.assertFalse(RefillRequest.objects.exists())

    def test_11_1b_signed_out_visitors_go_to_the_patient_login(self):
        self.client.logout()
        address = self.request_address(self.due_item)
        self.assertRedirects(self.client.post(address), f"/login/?next={address}")
