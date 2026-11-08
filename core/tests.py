from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import ActivityLog, Notification
from core.services import log_action, notifications_for, notify
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient

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

    def test_t4_a_medicine_has_plenty_of_stock(self):
        amoxicillin = Medicine.objects.get(name="Amoxicillin")
        batch = amoxicillin.batch_set.get()
        self.assertGreater(batch.quantity, amoxicillin.minimum_quantity)
        self.assertGreater(batch.expiry_date, self.today + timedelta(days=90))


class SeedDemoTwiceTests(TestCase):
    def test_t4_running_twice_creates_nothing_new(self):
        call_command("seed_demo")
        users = get_user_model().objects.count()
        patients = Patient.objects.count()
        drug_classes = DrugClass.objects.count()
        medicines = Medicine.objects.count()
        batches = Batch.objects.count()

        call_command("seed_demo")

        self.assertEqual(get_user_model().objects.count(), users)
        self.assertEqual(Patient.objects.count(), patients)
        self.assertEqual(DrugClass.objects.count(), drug_classes)
        self.assertEqual(Medicine.objects.count(), medicines)
        self.assertEqual(Batch.objects.count(), batches)


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
