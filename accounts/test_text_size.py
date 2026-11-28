"""SCRUM-68: patients can choose readable text across the patient interface."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.views import PATIENT_TEXT_SIZE_COOKIE


class PatientTextSizeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.patient = get_user_model().objects.create_user(
            username="samira", password="test-pass", role="patient"
        )
        cls.pharmacist = get_user_model().objects.create_user(
            username="rana", password="test-pass", role="pharmacist"
        )

    def setUp(self):
        self.client.force_login(self.patient)

    def choose_size(self, size, next_page="/home/"):
        return self.client.post(
            "/settings/text-size/", {"text_size": size, "next": next_page}
        )

    def test_default_has_accessible_controls_and_standard_selected(self):
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Choose text size"')
        self.assertContains(response, 'value="standard" class="patient-text-option selected"')
        self.assertNotContains(response, "patient-text-large")
        self.assertNotContains(response, "patient-text-extra-large")

    def test_large_size_persists_across_patient_pages_and_refresh(self):
        response = self.choose_size("large", "/family/?tab=care")
        self.assertRedirects(response, "/family/?tab=care", fetch_redirect_response=False)
        self.assertEqual(response.cookies[PATIENT_TEXT_SIZE_COOKIE].value, "large")

        for path in (
            "/home/", "/family/", "/patients/my/", "/notifications/",
            "/reservations/find/", "/prescriptions/mine/",
        ):
            with self.subTest(page=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, 'class="console-page patient-page patient-text-large ')
                self.assertContains(page, 'value="large" class="patient-text-option selected"')

        # The same client carries the browser cookie across page loads.
        self.assertContains(self.client.get("/home/"), "patient-text-large")

    def test_fullscreen_assistant_has_same_control_and_saved_setting(self):
        self.choose_size("extra_large")
        response = self.client.get("/assistant/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="chat-page patient-text-extra-large"')
        self.assertContains(response, 'aria-label="Choose text size"')
        self.assertContains(response, 'value="extra_large" class="patient-text-option selected"')

        # Change it from the assistant, then return to that same screen.
        self.choose_size("large", "/assistant/")
        response = self.client.get("/assistant/")
        self.assertContains(response, 'class="chat-page patient-text-large"')

    def test_extra_large_and_back_to_standard(self):
        large = self.choose_size("extra_large")
        self.assertEqual(large.cookies[PATIENT_TEXT_SIZE_COOKIE].value, "extra_large")
        self.assertContains(self.client.get("/home/"), "patient-text-extra-large")
        self.assertContains(
            self.client.get("/home/"), 'value="extra_large" class="patient-text-option selected"'
        )

        self.choose_size("standard")
        standard = self.client.get("/home/")
        self.assertNotContains(standard, "patient-text-extra-large")
        self.assertNotContains(standard, "patient-text-large")
        self.assertContains(standard, 'value="standard" class="patient-text-option selected"')

    def test_preference_cookie_has_safe_settings_and_one_year_lifetime(self):
        cookie = self.choose_size("large").cookies[PATIENT_TEXT_SIZE_COOKIE]
        self.assertEqual(cookie["max-age"], 365 * 24 * 60 * 60)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_rejects_invalid_size_without_setting_cookie(self):
        for invalid in ("", "tiny", "huge", "<script>"):
            with self.subTest(size=invalid):
                response = self.choose_size(invalid)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(PATIENT_TEXT_SIZE_COOKIE, response.cookies)

    def test_cannot_redirect_outside_this_site(self):
        response = self.choose_size("large", "https://evil.example/away")
        self.assertRedirects(response, "/home/", fetch_redirect_response=False)

    def test_post_only_and_restricted_to_patient_accounts(self):
        response = self.client.get("/settings/text-size/")
        self.assertEqual(response.status_code, 405)

        self.client.force_login(self.pharmacist)
        denied = self.choose_size("large")
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(PATIENT_TEXT_SIZE_COOKIE, denied.cookies)

    def test_anonymous_user_is_sent_to_patient_login(self):
        self.client.logout()
        response = self.choose_size("large")
        self.assertRedirects(
            response, "/login/?next=/settings/text-size/", fetch_redirect_response=False
        )

    def test_csrf_is_required_to_change_preference(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.patient)
        response = client.post("/settings/text-size/", {"text_size": "large"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(PATIENT_TEXT_SIZE_COOKIE, response.cookies)

    def test_pharmacy_staff_pages_do_not_have_patient_selector(self):
        self.client.force_login(self.pharmacist)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'aria-label="Choose text size"')
