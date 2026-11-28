import base64
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import CaregiverLink
from passport.models import Patient


# Tiny transparent PNG; profile-photo checks never need an external image service.
PNG_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X+CD5QAAAABJRU5ErkJggg=="
)


class FamilyPhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.omar = User.objects.create_user(username="omar", password="pass", role="patient")
        cls.other = User.objects.create_user(username="other", password="pass", role="patient")
        cls.staff = User.objects.create_user(username="staff", password="pass", role="pharmacist")
        cls.father = Patient.objects.create(name="Khalil Nassar", date_of_birth=date(1949, 11, 5))
        cls.link = CaregiverLink.objects.create(patient=cls.father, caregiver=cls.omar, active=True)

    def setUp(self):
        self.photos_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.photos_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.photos_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

    def photo(self, name="portrait.png", contents=PNG_PIXEL):
        return SimpleUploadedFile(name, contents, content_type="image/png")

    def patient_url(self):
        return f"/family/photo/{self.father.pk}/"

    def test_family_page_has_photo_buttons_and_encouragement(self):
        self.client.force_login(self.omar)
        response = self.client.get("/family/")
        self.assertContains(response, 'aria-label="Change your profile photo"')
        self.assertContains(response, f'data-family-photo-url="{self.patient_url()}"')
        self.assertContains(response, "You’re making a difference")
        self.assertContains(response, "Your support helps keep Khalil Nassar’s health on track.")

    def test_authorized_caregiver_uploads_patient_photo(self):
        self.client.force_login(self.omar)
        response = self.client.post(self.patient_url(), {"photo": self.photo()})
        self.assertRedirects(response, "/family/")
        self.father.refresh_from_db()
        self.assertTrue(self.father.profile_photo.name.startswith("profile_photos/patients/"))
        self.assertTrue(self.father.profile_photo.storage.exists(self.father.profile_photo.name))
        self.assertContains(self.client.get("/family/"), f'src="{self.father.profile_photo.url}"')
        # Django's test runner disables DEBUG and therefore the development media server.
        self.assertTrue(self.father.profile_photo.url.startswith("/media/profile_photos/patients/"))

    def test_caregiver_without_own_patient_record_can_upload_own_photo(self):
        self.client.force_login(self.omar)
        self.assertRedirects(self.client.post("/family/photo/me/", {"photo": self.photo()}), "/family/")
        self.omar.refresh_from_db()
        self.assertTrue(self.omar.profile_photo.name.startswith("profile_photos/users/"))
        self.assertContains(self.client.get("/family/"), 'class="profile-nav-photo"')

    def test_an_unlinked_user_cannot_change_another_patients_photo(self):
        self.client.force_login(self.other)
        response = self.client.post(self.patient_url(), {"photo": self.photo()})
        self.assertEqual(response.status_code, 404)
        self.father.refresh_from_db()
        self.assertFalse(self.father.profile_photo)

    def test_revoked_caregiver_cannot_change_photo(self):
        self.link.active = False
        self.link.save(update_fields=["active"])
        self.client.force_login(self.omar)
        self.assertEqual(self.client.post(self.patient_url(), {"photo": self.photo()}).status_code, 404)

    def test_staff_cannot_upload_family_photo(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.patient_url(), {"photo": self.photo()}).status_code, 403)

    def test_visitors_must_sign_in_to_upload_a_photo(self):
        self.assertRedirects(
            self.client.post(self.patient_url(), {"photo": self.photo()}),
            f"/login/?next={self.patient_url()}",
        )

    def test_upload_requires_post(self):
        self.client.force_login(self.omar)
        self.assertEqual(self.client.get(self.patient_url()).status_code, 405)

    def test_fake_image_or_svg_is_refused(self):
        self.client.force_login(self.omar)
        for name, content in (("fake.png", b"not a PNG"), ("portrait.svg", b"<svg/>",)):
            with self.subTest(name=name):
                response = self.client.post(self.patient_url(), {"photo": self.photo(name, content)}, follow=True)
                self.assertContains(response, "Upload a valid JPG, PNG, or WebP image.")
        self.father.refresh_from_db()
        self.assertFalse(self.father.profile_photo)

    def test_oversized_photo_is_refused(self):
        self.client.force_login(self.omar)
        large_image = PNG_PIXEL + b"x" * (2 * 1024 * 1024)
        response = self.client.post(self.patient_url(), {"photo": self.photo(contents=large_image)}, follow=True)
        self.assertContains(response, "Choose a photo smaller than 2 MB.")
        self.father.refresh_from_db()
        self.assertFalse(self.father.profile_photo)
