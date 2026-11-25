from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from reservations.models import Reservation
from reservations.services import (
    active_reservation_count,
    collect_reservation,
    expire_reservations,
    minutes_left,
    reservation_rows,
    reserve_medicine,
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
