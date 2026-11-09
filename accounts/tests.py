from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.permissions import is_pharmacy_staff
from accounts.services import create_patient_login


class UserModelTests(TestCase):
    def test_t2_user_model_is_the_carepharm_user(self):
        self.assertEqual(get_user_model()._meta.label, "accounts.User")

    def test_t2_roles_are_patient_pharmacist_assistant_owner(self):
        role_field = get_user_model()._meta.get_field("role")
        role_values = [value for value, label in role_field.choices]
        self.assertEqual(role_values, ["patient", "pharmacist", "assistant", "owner"])

    def test_t2_user_is_saved_with_its_role(self):
        get_user_model().objects.create_user(username="rana", password="pass", role="pharmacist")
        rana = get_user_model().objects.get(username="rana")
        self.assertEqual(rana.role, "pharmacist")

    def test_t2_new_user_is_a_patient_by_default(self):
        samira = get_user_model().objects.create_user(username="samira", password="pass")
        self.assertEqual(samira.role, "patient")

    def test_t2_is_pharmacist_is_off_by_default(self):
        karim = get_user_model().objects.create_user(username="karim", password="pass", role="assistant")
        self.assertFalse(karim.is_pharmacist)

    def test_t2_owner_can_also_be_a_pharmacist(self):
        get_user_model().objects.create_user(
            username="nadim", password="pass", role="owner", is_pharmacist=True
        )
        nadim = get_user_model().objects.get(username="nadim")
        self.assertEqual(nadim.role, "owner")
        self.assertTrue(nadim.is_pharmacist)


class PharmacyStaffTests(TestCase):
    def test_8_1_staff_roles_are_pharmacy_staff(self):
        for role in ["pharmacist", "assistant", "owner"]:
            self.assertTrue(is_pharmacy_staff(get_user_model()(role=role)))

    def test_8_1_a_patient_is_not_pharmacy_staff(self):
        self.assertFalse(is_pharmacy_staff(get_user_model()(role="patient")))


class SignInPageTests(TestCase):
    def test_8_1_the_site_opens_on_the_patient_login(self):
        self.assertRedirects(self.client.get("/"), "/login/")

    def test_8_1_patient_login_page_has_its_own_wording(self):
        response = self.client.get("/login/")
        self.assertContains(response, "For patients and families")
        self.assertContains(response, "Welcome back")

    def test_8_1_staff_login_page_has_its_own_wording(self):
        response = self.client.get("/login/staff/")
        self.assertContains(response, "For pharmacy staff")
        self.assertContains(response, "Welcome back")

    def test_8_1_each_login_page_links_to_the_other(self):
        self.assertContains(self.client.get("/login/"), 'href="/login/staff/"')
        self.assertContains(self.client.get("/login/staff/"), 'href="/login/"')


class SignInTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira = User.objects.create_user(username="samira", password="pass", role="patient")
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")
        cls.karim = User.objects.create_user(username="karim", password="pass", role="assistant")
        cls.nadim = User.objects.create_user(username="nadim", password="pass", role="owner")

    def assert_signed_in_as(self, user):
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def assert_nobody_is_signed_in(self):
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_8_1_patient_signs_in_on_the_patient_page(self):
        response = self.client.post("/login/", {"username": "samira", "password": "pass"})
        self.assertRedirects(response, "/login/")
        self.assert_signed_in_as(self.samira)

    def test_8_1_pharmacist_signs_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "rana", "password": "pass"})
        self.assertRedirects(response, "/login/staff/")
        self.assert_signed_in_as(self.rana)

    def test_8_1_assistant_signs_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "karim", "password": "pass"})
        self.assertRedirects(response, "/login/staff/")
        self.assert_signed_in_as(self.karim)

    def test_8_1_owner_signs_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "nadim", "password": "pass"})
        self.assertRedirects(response, "/login/staff/")
        self.assert_signed_in_as(self.nadim)

    def test_8_1_wrong_password_is_refused(self):
        response = self.client.post("/login/", {"username": "samira", "password": "wrong"})
        self.assertContains(response, "Wrong username or password.")
        self.assert_nobody_is_signed_in()

    def test_8_1_unknown_username_gets_the_same_message(self):
        response = self.client.post("/login/", {"username": "nobody", "password": "pass"})
        self.assertContains(response, "Wrong username or password.")
        self.assert_nobody_is_signed_in()

    def test_8_1_patient_cannot_sign_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "samira", "password": "pass"})
        self.assertContains(response, "This is not a staff account.")
        self.assert_nobody_is_signed_in()

    def test_8_1_staff_cannot_sign_in_on_the_patient_page(self):
        response = self.client.post("/login/", {"username": "rana", "password": "pass"})
        self.assertContains(response, "This is not a patient account.")
        self.assert_nobody_is_signed_in()

    def test_8_1_a_signed_in_user_sees_a_confirmation(self):
        self.client.force_login(self.rana)
        self.assertContains(self.client.get("/login/staff/"), "You are signed in as rana.")

    def test_8_1_a_signed_out_visitor_sees_no_confirmation(self):
        self.assertNotContains(self.client.get("/login/"), "You are signed in as")


class PatientLoginTests(TestCase):
    def test_3_1_a_new_login_is_a_patient_side_user(self):
        omar = create_patient_login("omar", "temporary1")
        self.assertEqual(omar.role, "patient")

    def test_3_1_a_new_login_signs_in_with_its_temporary_password(self):
        omar = create_patient_login("omar", "temporary1")
        self.assertTrue(omar.check_password("temporary1"))

    def test_3_1_a_taken_username_is_refused(self):
        create_patient_login("omar", "temporary1")
        with self.assertRaisesMessage(ValidationError, "That username is already taken."):
            create_patient_login("omar", "temporary2")
        self.assertEqual(get_user_model().objects.filter(username="omar").count(), 1)
