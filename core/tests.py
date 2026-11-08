from django.conf import settings
from django.test import SimpleTestCase

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

PHARMACY_SETTINGS = [
    "PHARMACY_NAME",
    "PHARMACY_ADDRESS",
    "PHARMACY_PHONE",
    "PHARMACY_HOURS",
]


class ProjectSettingsTests(SimpleTestCase):
    def test_t2_all_ten_apps_are_installed(self):
        for app in CAREPHARM_APPS:
            with self.subTest(app=app):
                self.assertIn(app, settings.INSTALLED_APPS)

    def test_t2_time_zone_is_beirut(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Beirut")
        self.assertTrue(settings.USE_TZ)

    def test_t2_any_host_is_allowed_without_env_file(self):
        self.assertIn("*", settings.ALLOWED_HOSTS)

    def test_t2_secret_key_has_a_default_without_env_file(self):
        self.assertTrue(settings.SECRET_KEY)

    def test_t2_pharmacy_details_are_in_settings(self):
        for name in PHARMACY_SETTINGS:
            with self.subTest(name=name):
                self.assertNotEqual(getattr(settings, name, ""), "")
