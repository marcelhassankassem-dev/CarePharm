from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import ActivityLog, Notification
from core.services import (
    activity_log,
    log_action,
    mark_all_read,
    notifications_for,
    notify,
    notify_patient,
    pharmacy_details,
    unread_count,
)
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient
from prescriptions.models import Prescription, PrescriptionItem
from reservations.models import RefillRequest, Reservation
from sales.models import Sale

NEW_MEDICINES = [
    ("Amlodipine", "5 mg", "Calcium channel blockers"),
    ("Omeprazole", "20 mg", "Proton pump inhibitors"),
    ("Lisinopril", "10 mg", "ACE inhibitors"),
    ("Losartan", "50 mg", "Angiotensin receptor blockers"),
    ("Cetirizine", "10 mg", "Antihistamines"),
    ("Salbutamol inhaler", "100 mcg", "Bronchodilators"),
    ("Levothyroxine", "50 mcg", "Thyroid hormones"),
    ("Aspirin", "81 mg", "NSAIDs"),
    ("Azithromycin", "250 mg", "Macrolides"),
    ("Ampicillin", "250 mg", "Penicillins"),
    ("Paracetamol syrup", "120 mg/5 ml", "Analgesics"),
    ("Metformin", "850 mg", "Biguanides"),
]

CAREPHARM_APPS = [
    "accounts",
    "passport",
    "prescriptions",
    "safety",
    "inventory",
    "sales",
    "reservations",
    "assistant",
    "reports",
    "core",
]


class ProjectSettingsTests(SimpleTestCase):
    def test_t2_all_ten_apps_are_installed(self):
        for app in CAREPHARM_APPS:
            self.assertIn(app, settings.INSTALLED_APPS)

    def test_t2_time_zone_is_beirut(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Beirut")
        self.assertTrue(settings.USE_TZ)

    def test_t2_any_host_is_allowed_without_env_file(self):
        self.assertIn("*", settings.ALLOWED_HOSTS)

    def test_t2_secret_key_has_a_default_without_env_file(self):
        self.assertTrue(settings.SECRET_KEY)

    def test_t2_pharmacy_details_are_in_settings(self):
        self.assertTrue(settings.PHARMACY_NAME)
        self.assertTrue(settings.PHARMACY_ADDRESS)
        self.assertTrue(settings.PHARMACY_PHONE)
        self.assertTrue(settings.PHARMACY_HOURS)

    def test_p1_pharmacy_details_come_from_the_settings(self):
        self.assertEqual(
            pharmacy_details(),
            {
                "name": settings.PHARMACY_NAME,
                "address": settings.PHARMACY_ADDRESS,
                "phone": settings.PHARMACY_PHONE,
                "hours": settings.PHARMACY_HOURS,
            },
        )


class SeedDemoPeopleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def test_t4_staff_have_their_roles(self):
        User = get_user_model()
        self.assertEqual(User.objects.get(username="nadim").role, "owner")
        self.assertEqual(User.objects.get(username="rana").role, "pharmacist")
        self.assertEqual(User.objects.get(username="karim").role, "assistant")

    def test_t4_nadim_is_also_a_pharmacist(self):
        nadim = get_user_model().objects.get(username="nadim")
        self.assertTrue(nadim.is_pharmacist)

    def test_t4_only_the_owner_can_open_the_admin(self):
        User = get_user_model()
        self.assertTrue(User.objects.get(username="nadim").is_staff)
        self.assertFalse(User.objects.get(username="rana").is_staff)
        self.assertFalse(User.objects.get(username="karim").is_staff)

    def test_t4_patients_with_logins_are_linked_to_their_users(self):
        for username in ["samira", "maya", "omars_father"]:
            user = get_user_model().objects.get(username=username)
            self.assertEqual(user.role, "patient")
            self.assertTrue(Patient.objects.filter(user=user).exists())

    def test_t4_omar_has_a_login_but_no_patient_record(self):
        omar = get_user_model().objects.get(username="omar")
        self.assertEqual(omar.role, "patient")
        self.assertFalse(Patient.objects.filter(user=omar).exists())

    def test_t4_ahmad_is_a_patient_with_no_login(self):
        ahmad = Patient.objects.get(name="Ahmad")
        self.assertIsNone(ahmad.user)

    def test_t4_demo_users_can_sign_in(self):
        for username in ["nadim", "rana", "karim", "samira", "maya", "omar", "omars_father"]:
            self.assertTrue(self.client.login(username=username, password="carepharm123"))

    def test_8_2_staff_have_full_names(self):
        User = get_user_model()
        self.assertEqual(User.objects.get(username="nadim").get_full_name(), "Nadim Khoury")
        self.assertEqual(User.objects.get(username="rana").get_full_name(), "Rana Haddad")
        self.assertEqual(User.objects.get(username="karim").get_full_name(), "Karim Saad")


class SeedDemoStockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def setUp(self):
        self.today = timezone.localdate()

    def test_t4_a_medicine_has_low_stock(self):
        ibuprofen = Medicine.objects.get(name="Ibuprofen")
        batches = ibuprofen.batch_set.filter(expiry_date__gte=self.today)
        available = sum(batch.quantity for batch in batches)
        self.assertLess(available, ibuprofen.minimum_quantity)

    def test_t4_a_batch_is_near_expiry(self):
        last_day = self.today + timedelta(days=90)
        batches = Batch.objects.filter(expiry_date__gte=self.today, expiry_date__lte=last_day)
        self.assertTrue(batches.exists())

    def test_t4_a_batch_has_expired(self):
        self.assertTrue(Batch.objects.filter(expiry_date__lt=self.today).exists())

    def test_7_2_a_batch_expired_in_the_last_week(self):
        last_week = self.today - timedelta(days=7)
        batches = Batch.objects.filter(expiry_date__gte=last_week, expiry_date__lt=self.today)
        self.assertTrue(batches.exists())

    def test_t4_a_medicine_has_plenty_of_stock(self):
        amoxicillin = Medicine.objects.get(name="Amoxicillin")
        batch = amoxicillin.batch_set.get()
        self.assertGreater(batch.quantity, amoxicillin.minimum_quantity)
        self.assertGreater(batch.expiry_date, self.today + timedelta(days=90))

    def test_4_1_demo_batches_were_not_received_today(self):
        self.assertFalse(Batch.objects.filter(received_at__date=self.today).exists())


class SeedDemoMedicineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def test_t8_the_new_medicines_have_the_right_drug_class(self):
        for name, strength, drug_class in NEW_MEDICINES:
            medicine = Medicine.objects.get(name=name, strength=strength)
            self.assertEqual(medicine.drug_class.name, drug_class)

    def test_t8_a_penicillin_allergy_matches_more_than_one_medicine(self):
        penicillins = Medicine.objects.filter(drug_class__name="Penicillins")
        self.assertEqual(sorted(medicine.name for medicine in penicillins), ["Amoxicillin", "Ampicillin"])

    def test_t8_ibuprofen_and_aspirin_share_a_drug_class(self):
        nsaids = Medicine.objects.filter(drug_class__name="NSAIDs")
        self.assertEqual(sorted(medicine.name for medicine in nsaids), ["Aspirin", "Ibuprofen"])

    def test_t8_metformin_comes_in_two_strengths(self):
        metformin = Medicine.objects.filter(name="Metformin")
        self.assertEqual(sorted(medicine.strength for medicine in metformin), ["500 mg", "850 mg"])

    def test_t8_metformin_850_has_no_available_stock(self):
        metformin = Medicine.objects.get(name="Metformin", strength="850 mg")
        batches = metformin.batch_set.filter(expiry_date__gte=timezone.localdate())
        self.assertEqual(sum(batch.quantity for batch in batches), 0)

    def test_t8_ahmad_still_takes_metformin_500(self):
        current_medicine = CurrentMedicine.objects.get(patient__name="Ahmad")
        self.assertEqual(str(current_medicine.medicine), "Metformin 500 mg")

    def test_t8_samira_still_takes_metformin_500(self):
        current_medicine = CurrentMedicine.objects.get(
            patient__name="Samira",
            medicine__name="Metformin",
        )
        self.assertEqual(current_medicine.medicine.strength, "500 mg")

    def test_t8_only_ibuprofen_and_metformin_850_are_low_on_stock(self):
        low_on_stock = []
        for medicine in Medicine.objects.all():
            batches = medicine.batch_set.filter(expiry_date__gte=timezone.localdate())
            available = sum(batch.quantity for batch in batches)
            if available < medicine.minimum_quantity:
                low_on_stock.append(str(medicine))
        self.assertEqual(sorted(low_on_stock), ["Ibuprofen 400 mg", "Metformin 850 mg"])


class SeedDemoPassportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def test_3_1_ahmad_is_allergic_to_penicillins(self):
        allergy = Allergy.objects.get(patient__name="Ahmad")
        self.assertEqual(allergy.drug_class.name, "Penicillins")

    def test_3_1_ahmad_takes_metformin(self):
        current_medicine = CurrentMedicine.objects.get(patient__name="Ahmad")
        self.assertEqual(current_medicine.medicine.name, "Metformin")

    def test_3_1_samira_takes_metformin_and_atorvastatin(self):
        current_medicines = CurrentMedicine.objects.filter(patient__name="Samira")
        names = sorted(current_medicine.medicine.name for current_medicine in current_medicines)
        self.assertEqual(names, ["Atorvastatin", "Metformin"])

    def test_3_1_maya_has_nothing_recorded(self):
        self.assertFalse(Allergy.objects.filter(patient__name="Maya").exists())
        self.assertFalse(CurrentMedicine.objects.filter(patient__name="Maya").exists())


class SeedDemoPrescriptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def test_9_2_maya_is_told_her_prescription_is_ready(self):
        notifications = Notification.objects.filter(user__username="maya", text__contains="ready for pickup")
        self.assertEqual(notifications.count(), 1)
        self.assertFalse(notifications.get().read)

    def test_9_2_samiras_notification_for_her_collected_prescription_is_already_read(self):
        notifications = Notification.objects.filter(user__username="samira", text__contains="ready for pickup")
        self.assertTrue(notifications.exists())
        self.assertFalse(notifications.filter(read=False).exists())

    def test_9_2_omars_father_is_still_told_about_the_prescription_waiting_for_him(self):
        unread = Notification.objects.filter(
            user__username="omars_father", text__contains="ready for pickup", read=False
        )
        self.assertEqual([notification.text.split(": ")[1] for notification in unread], ["Atorvastatin 20 mg."])

    def test_1_1_every_status_before_returned_has_a_demo_prescription(self):
        statuses = set(Prescription.objects.values_list("status", flat=True))
        for status in ["received", "preparing", "ready", "collected"]:
            self.assertIn(status, statuses)

    def test_1_1_prescriptions_not_yet_ready_have_taken_no_stock(self):
        items = PrescriptionItem.objects.filter(prescription__status__in=["received", "preparing"])
        self.assertTrue(items.exists())
        self.assertFalse(items.filter(batch__isnull=False).exists())

    def test_1_1_ready_and_collected_prescriptions_took_their_stock(self):
        items = PrescriptionItem.objects.filter(prescription__status__in=["ready", "collected"])
        self.assertTrue(items.exists())
        self.assertFalse(items.filter(batch__isnull=True).exists())

    def test_1_1_a_ready_prescription_has_waited_more_than_2_days(self):
        two_days_ago = timezone.now() - timedelta(days=2)
        waiting = Prescription.objects.filter(status="ready", ready_at__lt=two_days_ago)
        self.assertTrue(waiting.exists())

    def test_1_1_each_collected_prescription_has_its_sale(self):
        for collected in Prescription.objects.filter(status="collected"):
            sales = Sale.objects.filter(source="prescription", patient=collected.patient, time=collected.collected_at)
            self.assertEqual(sales.count(), 1)

    def test_8_3_each_seeded_prescription_sale_is_logged_when_it_was_collected(self):
        for collected in Prescription.objects.filter(status="collected"):
            sale = Sale.objects.get(source="prescription", patient=collected.patient, time=collected.collected_at)
            entry = ActivityLog.objects.get(action="Recorded sale", details__startswith=f"Sale {sale.pk} ·")
            self.assertEqual(entry.time, collected.collected_at)

    def test_8_3_stock_is_logged_on_the_day_each_prescription_became_ready(self):
        items = PrescriptionItem.objects.filter(prescription__status__in=["ready", "collected"])
        for item in items.select_related("prescription", "batch"):
            entry = ActivityLog.objects.get(action="Took stock", details__contains=f"batch {item.batch.batch_number} ")
            self.assertEqual(timezone.localdate(entry.time), timezone.localdate(item.prescription.ready_at))


class SeedDemoSalesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def counter_sales(self):
        return Sale.objects.filter(source="counter")

    def test_7_1_the_demo_has_a_counter_sale_today(self):
        self.assertTrue(self.counter_sales().filter(time__date=timezone.localdate()).exists())

    def test_7_1_the_demo_has_counter_sales_on_several_days(self):
        days = {timezone.localdate(sale.time) for sale in self.counter_sales()}
        self.assertGreaterEqual(len(days), 3)

    def test_7_1_demo_counter_sales_were_made_by_karim(self):
        self.assertEqual({sale.staff.username for sale in self.counter_sales()}, {"karim"})

    def test_7_1_each_demo_sale_is_logged_on_its_own_day(self):
        for sale in self.counter_sales():
            entry = ActivityLog.objects.get(action="Recorded sale", details__startswith=f"Sale {sale.pk} ·")
            self.assertEqual(timezone.localdate(entry.time), timezone.localdate(sale.time))

    def test_7_1_demo_counter_sales_took_their_stock(self):
        taken = ActivityLog.objects.filter(action="Took stock", user__username="karim")
        lines = sum(sale.saleitem_set.count() for sale in self.counter_sales())
        self.assertEqual(taken.count(), lines)


class SeedDemoReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def setUp(self):
        self.two_hours_ago = timezone.now() - timedelta(hours=2)

    def test_10_3_the_demo_has_two_reservations_still_held(self):
        held = Reservation.objects.filter(status="active", created_at__gt=self.two_hours_ago)
        self.assertEqual(held.count(), 2)

    def test_10_3_one_demo_reservation_ends_within_30_minutes(self):
        soon = timezone.now() - timedelta(minutes=90)
        self.assertTrue(Reservation.objects.filter(created_at__gt=self.two_hours_ago, created_at__lte=soon).exists())

    def test_t7_one_demo_reservation_is_past_its_2_hours_and_waits_for_a_page_load(self):
        past = Reservation.objects.filter(status="active", created_at__lte=self.two_hours_ago)
        self.assertEqual(past.count(), 1)

    def test_10_3_demo_reservations_belong_to_patients_with_logins(self):
        for reservation in Reservation.objects.all():
            self.assertIsNotNone(reservation.patient.user)

    def test_10_3_demo_reservations_took_their_stock_when_they_were_made(self):
        for reservation in Reservation.objects.all():
            entry = ActivityLog.objects.get(action="Took stock", user=reservation.patient.user, time=reservation.created_at)
            self.assertIn(f"batch {reservation.batch.batch_number}", entry.details)


class SeedDemoRefillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")

    def available(self, medicine):
        batches = medicine.batch_set.filter(expiry_date__gte=timezone.localdate())
        return sum(batch.quantity for batch in batches)

    def test_11_2_the_demo_has_two_open_refill_requests_from_omars_father(self):
        requests = RefillRequest.objects.filter(status__in=["waiting", "delayed"])
        self.assertEqual(requests.count(), 2)
        self.assertEqual({request.requested_by.username for request in requests}, {"omars_father"})

    def test_11_4_the_out_of_stock_demo_request_is_delayed_with_a_date(self):
        delayed = RefillRequest.objects.get(status="delayed")
        self.assertEqual(str(delayed.prescription_item.medicine), "Metformin 850 mg")
        self.assertGreater(delayed.expected_on, timezone.localdate())
        self.assertEqual(RefillRequest.objects.filter(status="waiting").count(), 1)

    def test_11_4_the_delayed_demo_request_is_still_the_older_one(self):
        delayed = RefillRequest.objects.get(status="delayed")
        new = RefillRequest.objects.get(status="waiting")
        self.assertLess(delayed.created_at, new.created_at - timedelta(hours=12))

    def test_11_4_omars_father_was_told_about_the_delay(self):
        delayed = RefillRequest.objects.get(status="delayed")
        notification = Notification.objects.get(user__username="omars_father", text__startswith="Refill delayed")
        self.assertEqual(
            notification.text,
            f"Refill delayed: Metformin 850 mg, expected {delayed.expected_on:%d %b %Y}.",
        )

    def test_11_2_demo_requests_point_at_collected_long_term_items(self):
        for request in RefillRequest.objects.select_related("prescription_item__prescription"):
            self.assertEqual(request.prescription_item.prescription.status, "collected")
            self.assertTrue(request.prescription_item.long_term)

    def test_11_2_the_demo_requests_were_made_at_different_times(self):
        times = RefillRequest.objects.values_list("created_at", flat=True)
        self.assertEqual(len(set(times)), 2)

    def test_11_3_one_demo_request_is_for_a_medicine_with_no_stock(self):
        requests = RefillRequest.objects.select_related("prescription_item__medicine")
        empty = [request for request in requests if self.available(request.prescription_item.medicine) == 0]
        self.assertEqual(len(empty), 1)
        self.assertEqual(str(empty[0].prescription_item.medicine), "Metformin 850 mg")

    def test_11_2_samira_has_no_refill_request_yet(self):
        self.assertFalse(RefillRequest.objects.filter(prescription_item__prescription__patient__name="Samira").exists())


class SeedDemoTwiceTests(TestCase):
    def test_t4_running_twice_creates_nothing_new(self):
        call_command("seed_demo")
        users = get_user_model().objects.count()
        patients = Patient.objects.count()
        drug_classes = DrugClass.objects.count()
        medicines = Medicine.objects.count()
        batches = Batch.objects.count()
        allergies = Allergy.objects.count()
        current_medicines = CurrentMedicine.objects.count()
        prescriptions = Prescription.objects.count()
        sales = Sale.objects.count()
        reservations = Reservation.objects.count()
        refill_requests = RefillRequest.objects.count()
        quantities = list(Batch.objects.order_by("id").values_list("quantity", flat=True))

        call_command("seed_demo")

        self.assertEqual(get_user_model().objects.count(), users)
        self.assertEqual(Patient.objects.count(), patients)
        self.assertEqual(DrugClass.objects.count(), drug_classes)
        self.assertEqual(Medicine.objects.count(), medicines)
        self.assertEqual(Batch.objects.count(), batches)
        self.assertEqual(Allergy.objects.count(), allergies)
        self.assertEqual(CurrentMedicine.objects.count(), current_medicines)
        self.assertEqual(Prescription.objects.count(), prescriptions)
        self.assertEqual(Sale.objects.count(), sales)
        self.assertEqual(Reservation.objects.count(), reservations)
        self.assertEqual(RefillRequest.objects.count(), refill_requests)
        self.assertEqual(list(Batch.objects.order_by("id").values_list("quantity", flat=True)), quantities)


class ActivityLogTests(TestCase):
    def setUp(self):
        self.karim = get_user_model().objects.create_user(username="karim", password="pass")

    def test_8_3_log_action_saves_who_did_what(self):
        log_action(self.karim, "Sale recorded", "2 x Amoxicillin 500 mg")
        entry = ActivityLog.objects.get()
        self.assertEqual(entry.user, self.karim)
        self.assertEqual(entry.action, "Sale recorded")
        self.assertEqual(entry.details, "2 x Amoxicillin 500 mg")

    def test_8_3_details_are_optional(self):
        log_action(self.karim, "Sale recorded")
        self.assertEqual(ActivityLog.objects.get().details, "")

    def test_8_3_system_actions_have_no_user(self):
        log_action(None, "Reservation expired")
        self.assertIsNone(ActivityLog.objects.get().user)

    def test_8_3_time_is_set_automatically(self):
        log_action(self.karim, "Sale recorded")
        self.assertIsNotNone(ActivityLog.objects.get().time)

    def test_8_3_deleting_a_user_keeps_their_log_entries(self):
        log_action(self.karim, "Sale recorded")
        self.karim.delete()
        self.assertIsNone(ActivityLog.objects.get().user)


class ActivityLogListTests(TestCase):
    def setUp(self):
        self.karim = get_user_model().objects.create_user(username="karim", password="pass")

    def log_at(self, action, time):
        entry = log_action(self.karim, action)
        ActivityLog.objects.filter(pk=entry.pk).update(time=time)
        entry.refresh_from_db()
        return entry

    def test_8_3_the_log_is_newest_first(self):
        older = self.log_at("Recorded delivery", timezone.now() - timedelta(hours=2))
        newer = log_action(self.karim, "Took stock")
        self.assertEqual(list(activity_log()), [newer, older])

    def test_8_3_the_whole_log_keeps_earlier_days(self):
        older = self.log_at("Recorded delivery", timezone.now() - timedelta(days=3))
        self.assertEqual(list(activity_log()), [older])

    def test_8_3_today_only_leaves_out_earlier_days(self):
        self.log_at("Recorded delivery", timezone.now() - timedelta(days=1))
        today = log_action(self.karim, "Took stock")
        self.assertEqual(list(activity_log(today_only=True)), [today])

    def test_8_3_today_starts_at_midnight_in_beirut(self):
        just_after_midnight = timezone.localtime().replace(hour=0, minute=30, second=0, microsecond=0)
        entry = self.log_at("Recorded delivery", just_after_midnight)
        self.assertEqual(list(activity_log(today_only=True)), [entry])


class NotificationTests(TestCase):
    def setUp(self):
        self.samira = get_user_model().objects.create_user(username="samira", password="pass")

    def test_t6_notify_saves_the_user_text_and_link(self):
        notify(self.samira, "Your prescription is ready", "/prescriptions/1/")
        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.samira)
        self.assertEqual(notification.text, "Your prescription is ready")
        self.assertEqual(notification.link, "/prescriptions/1/")

    def test_t6_new_notification_is_unread(self):
        notify(self.samira, "Your prescription is ready", "/prescriptions/1/")
        self.assertFalse(Notification.objects.get().read)

    def test_t6_users_only_see_their_own_notifications(self):
        maya = get_user_model().objects.create_user(username="maya", password="pass")
        notify(self.samira, "For Samira", "/a/")
        notify(maya, "For Maya", "/b/")
        texts = [notification.text for notification in notifications_for(self.samira)]
        self.assertEqual(texts, ["For Samira"])

    def test_t6_unread_notifications_come_first(self):
        first = notify(self.samira, "Read", "/a/")
        first.read = True
        first.save()
        notify(self.samira, "Unread", "/b/")
        texts = [notification.text for notification in notifications_for(self.samira)]
        self.assertEqual(texts, ["Unread", "Read"])

    def test_t6_newest_notifications_come_first_among_unread(self):
        notify(self.samira, "Older", "/a/")
        notify(self.samira, "Newer", "/b/")
        texts = [notification.text for notification in notifications_for(self.samira)]
        self.assertEqual(texts, ["Newer", "Older"])

    def test_9_2_unread_notifications_are_counted(self):
        maya = get_user_model().objects.create_user(username="maya", password="pass")
        notify(self.samira, "One", "/a/")
        notify(self.samira, "Two", "/b/")
        notify(maya, "Maya's", "/c/")
        self.assertEqual(unread_count(self.samira), 2)

    def test_9_2_marking_all_read_only_touches_that_users_notifications(self):
        maya = get_user_model().objects.create_user(username="maya", password="pass")
        notify(self.samira, "One", "/a/")
        notify(maya, "Maya's", "/c/")
        mark_all_read(self.samira)
        self.assertEqual(unread_count(self.samira), 0)
        self.assertEqual(unread_count(maya), 1)


class NotificationsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.maya = User.objects.create_user(username="maya", password="pass", role="patient")
        Patient.objects.create(name="Maya", date_of_birth="1990-06-02", user=cls.maya)
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")

    def setUp(self):
        self.client.force_login(self.maya)

    def test_9_2_signed_out_visitors_go_to_the_patient_sign_in(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/notifications/"), "/login/?next=/notifications/")

    def test_9_2_staff_cannot_open_the_patients_notifications(self):
        self.client.force_login(self.rana)
        self.assertEqual(self.client.get("/notifications/").status_code, 403)

    def test_9_2_the_page_lists_the_notifications_with_their_links(self):
        notify(self.maya, "Maya's prescription is ready for pickup.", "/prescriptions/mine/")
        response = self.client.get("/notifications/")
        self.assertContains(response, "Notifications")
        self.assertContains(response, "Maya&#x27;s prescription is ready for pickup.")
        self.assertContains(response, 'href="/prescriptions/mine/"')

    def test_9_2_unread_notifications_come_first_and_stand_out(self):
        old = notify(self.maya, "Seen before", "/a/")
        old.read = True
        old.save()
        notify(self.maya, "Not seen yet", "/b/")
        page = self.client.get("/notifications/").content.decode()
        self.assertLess(page.index("Not seen yet"), page.index("Seen before"))
        self.assertEqual(page.count('class="notification unread"'), 1)

    def test_9_2_opening_the_page_marks_everything_read(self):
        notify(self.maya, "Not seen yet", "/b/")
        self.client.get("/notifications/")
        self.assertEqual(unread_count(self.maya), 0)

    def test_9_2_other_users_notifications_are_not_shown(self):
        notify(self.rana, "For Rana", "/a/")
        self.assertNotContains(self.client.get("/notifications/"), "For Rana")

    def test_9_2_no_notifications_says_so(self):
        self.assertContains(self.client.get("/notifications/"), "No notifications yet.")


class NotifyPatientTests(TestCase):
    def test_11_4_a_patient_with_a_login_is_notified(self):
        samira_user = get_user_model().objects.create_user(username="samira", password="pass")
        samira = Patient.objects.create(name="Samira", date_of_birth="1958-03-14", user=samira_user)
        told = notify_patient(samira, "Refill delayed", "/")
        notification = Notification.objects.get()
        self.assertEqual((notification.user, notification.text, notification.link), (samira_user, "Refill delayed", "/"))
        self.assertEqual(told, [samira_user])

    def test_11_4_a_patient_with_no_login_is_not_notified(self):
        ahmad = Patient.objects.create(name="Ahmad", date_of_birth="1980-06-02")
        self.assertEqual(notify_patient(ahmad, "Refill delayed", "/"), [])
        self.assertFalse(Notification.objects.exists())
