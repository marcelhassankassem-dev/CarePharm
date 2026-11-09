from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.permissions import can_act_as_pharmacist, is_owner, is_pharmacy_staff
from accounts.services import create_patient_login

NEW_STAFF = {
    "first_name": "Hala",
    "last_name": "Nader",
    "username": "hala",
    "password": "temporary-pass",
    "role": "pharmacist",
}


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
        self.assertRedirects(response, "/staff/")
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


class RolePermissionTests(TestCase):
    def test_8_2_only_the_owner_role_is_owner(self):
        self.assertTrue(is_owner(get_user_model()(role="owner")))
        for role in ["pharmacist", "assistant", "patient"]:
            self.assertFalse(is_owner(get_user_model()(role=role)))

    def test_8_2_a_pharmacist_can_act_as_pharmacist(self):
        self.assertTrue(can_act_as_pharmacist(get_user_model()(role="pharmacist")))

    def test_8_2_an_owner_who_is_a_pharmacist_can_act_as_pharmacist(self):
        self.assertTrue(can_act_as_pharmacist(get_user_model()(role="owner", is_pharmacist=True)))

    def test_8_2_an_owner_who_is_not_a_pharmacist_cannot_act_as_pharmacist(self):
        self.assertFalse(can_act_as_pharmacist(get_user_model()(role="owner")))

    def test_8_2_assistants_and_patients_cannot_act_as_pharmacist(self):
        self.assertFalse(can_act_as_pharmacist(get_user_model()(role="assistant")))
        self.assertFalse(can_act_as_pharmacist(get_user_model()(role="patient")))


class StaffPagesTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.nadim = User.objects.create_user(
            username="nadim",
            password="pass",
            role="owner",
            is_pharmacist=True,
            first_name="Nadim",
            last_name="Khoury",
        )
        cls.rana = User.objects.create_user(
            username="rana",
            password="pass",
            role="pharmacist",
            first_name="Rana",
            last_name="Haddad",
        )
        cls.karim = User.objects.create_user(
            username="karim",
            password="pass",
            role="assistant",
            first_name="Karim",
            last_name="Saad",
        )
        cls.samira = User.objects.create_user(
            username="samira",
            password="pass",
            role="patient",
            first_name="Samira",
            last_name="Itani",
        )


class StaffPageAccessTests(StaffPagesTestData):
    def staff_page_addresses(self):
        return ["/staff/", "/staff/add/", f"/staff/{self.karim.pk}/edit/"]

    def test_8_2_signed_out_visitors_go_to_the_login_page(self):
        for address in self.staff_page_addresses():
            self.assertRedirects(self.client.get(address), f"/login/?next={address}")

    def test_8_2_the_owner_can_open_the_staff_pages(self):
        self.client.force_login(self.nadim)
        for address in self.staff_page_addresses():
            self.assertEqual(self.client.get(address).status_code, 200)

    def test_8_2_a_pharmacist_is_refused(self):
        self.client.force_login(self.rana)
        for address in self.staff_page_addresses():
            self.assertEqual(self.client.get(address).status_code, 403)

    def test_8_2_an_assistant_is_refused(self):
        self.client.force_login(self.karim)
        for address in self.staff_page_addresses():
            self.assertEqual(self.client.get(address).status_code, 403)

    def test_8_2_a_patient_is_refused(self):
        self.client.force_login(self.samira)
        for address in self.staff_page_addresses():
            self.assertEqual(self.client.get(address).status_code, 403)


class StaffListTests(StaffPagesTestData):
    def setUp(self):
        self.client.force_login(self.nadim)

    def test_8_2_the_list_shows_staff_names_and_roles(self):
        response = self.client.get("/staff/")
        self.assertContains(response, "Staff and Roles")
        self.assertContains(response, "Rana Haddad")
        self.assertContains(response, "Karim Saad")
        self.assertContains(response, "Pharmacist")
        self.assertContains(response, "Assistant")

    def test_8_2_the_list_leaves_out_patients(self):
        self.assertNotContains(self.client.get("/staff/"), "Samira Itani")

    def test_8_2_the_list_counts_who_can_sign_in(self):
        self.assertContains(self.client.get("/staff/"), "3 people can sign in to this pharmacy")

    def test_8_2_a_disabled_account_is_shown_and_not_counted(self):
        self.karim.is_active = False
        self.karim.save()
        response = self.client.get("/staff/")
        self.assertContains(response, "Disabled")
        self.assertContains(response, "2 people can sign in to this pharmacy")

    def test_8_2_someone_who_never_signed_in_shows_never(self):
        self.assertContains(self.client.get("/staff/"), "Never")

    def test_8_2_only_non_owners_have_an_edit_link(self):
        response = self.client.get("/staff/")
        self.assertContains(response, f'href="/staff/{self.karim.pk}/edit/"')
        self.assertNotContains(response, f'href="/staff/{self.nadim.pk}/edit/"')

    def test_8_2_the_page_links_to_adding_a_staff_member(self):
        self.assertContains(self.client.get("/staff/"), 'href="/staff/add/"')

    def test_8_2_the_page_shows_the_role_permissions(self):
        response = self.client.get("/staff/")
        self.assertContains(response, "Role Permissions")
        self.assertContains(response, "Continue past a safety warning")
        self.assertContains(response, "Manage staff accounts")


class AddStaffTests(StaffPagesTestData):
    def setUp(self):
        self.client.force_login(self.nadim)

    def assert_nobody_was_added(self):
        self.assertFalse(get_user_model().objects.filter(username="hala").exists())

    def test_8_2_adding_a_staff_member_saves_the_account(self):
        response = self.client.post("/staff/add/", NEW_STAFF)
        self.assertRedirects(response, "/staff/")
        hala = get_user_model().objects.get(username="hala")
        self.assertEqual(hala.get_full_name(), "Hala Nader")
        self.assertEqual(hala.role, "pharmacist")
        self.assertTrue(hala.is_active)

    def test_8_2_an_assistant_can_be_added(self):
        self.client.post("/staff/add/", {**NEW_STAFF, "role": "assistant"})
        self.assertEqual(get_user_model().objects.get(username="hala").role, "assistant")

    def test_8_2_the_new_member_can_sign_in_with_the_temporary_password(self):
        self.client.post("/staff/add/", NEW_STAFF)
        self.client.logout()
        response = self.client.post(
            "/login/staff/", {"username": "hala", "password": "temporary-pass"}
        )
        self.assertRedirects(response, "/login/staff/")

    def test_8_2_a_new_member_cannot_open_the_django_admin(self):
        self.client.post("/staff/add/", NEW_STAFF)
        hala = get_user_model().objects.get(username="hala")
        self.assertFalse(hala.is_staff)
        self.assertFalse(hala.is_superuser)

    def test_8_2_a_new_member_cannot_be_an_owner(self):
        self.client.post("/staff/add/", {**NEW_STAFF, "role": "owner"})
        self.assert_nobody_was_added()

    def test_8_2_a_new_member_cannot_be_a_patient(self):
        self.client.post("/staff/add/", {**NEW_STAFF, "role": "patient"})
        self.assert_nobody_was_added()

    def test_8_2_a_taken_username_is_refused(self):
        response = self.client.post("/staff/add/", {**NEW_STAFF, "username": "rana"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_user_model().objects.filter(username="rana").count(), 1)
        self.assertEqual(get_user_model().objects.get(username="rana").first_name, "Rana")

    def test_8_2_a_short_password_is_refused(self):
        self.client.post("/staff/add/", {**NEW_STAFF, "password": "short"})
        self.assert_nobody_was_added()

    def test_8_2_a_missing_name_is_refused(self):
        self.client.post("/staff/add/", {**NEW_STAFF, "first_name": ""})
        self.assert_nobody_was_added()

    def test_8_2_someone_who_is_not_the_owner_cannot_add_staff(self):
        self.client.force_login(self.rana)
        response = self.client.post("/staff/add/", NEW_STAFF)
        self.assertEqual(response.status_code, 403)
        self.assert_nobody_was_added()


class EditStaffTests(StaffPagesTestData):
    def setUp(self):
        self.client.force_login(self.nadim)

    def edit_karim(self, data):
        return self.client.post(f"/staff/{self.karim.pk}/edit/", data)

    def test_8_2_the_edit_page_shows_who_is_being_edited(self):
        self.assertContains(self.client.get(f"/staff/{self.karim.pk}/edit/"), "Karim Saad")

    def test_8_2_the_role_can_be_changed(self):
        response = self.edit_karim({"role": "pharmacist", "is_active": "on"})
        self.assertRedirects(response, "/staff/")
        self.karim.refresh_from_db()
        self.assertEqual(self.karim.role, "pharmacist")
        self.assertTrue(self.karim.is_active)

    def test_8_2_an_account_can_be_turned_off(self):
        self.edit_karim({"role": "assistant"})
        self.karim.refresh_from_db()
        self.assertFalse(self.karim.is_active)

    def test_8_2_an_account_can_be_turned_back_on(self):
        self.edit_karim({"role": "assistant"})
        self.edit_karim({"role": "assistant", "is_active": "on"})
        self.karim.refresh_from_db()
        self.assertTrue(self.karim.is_active)

    def test_8_2_a_staff_member_cannot_be_made_an_owner(self):
        self.edit_karim({"role": "owner", "is_active": "on"})
        self.karim.refresh_from_db()
        self.assertEqual(self.karim.role, "assistant")

    def test_8_2_the_owner_cannot_be_edited(self):
        address = f"/staff/{self.nadim.pk}/edit/"
        self.assertEqual(self.client.get(address).status_code, 404)
        self.assertEqual(self.client.post(address, {"role": "assistant"}).status_code, 404)
        self.nadim.refresh_from_db()
        self.assertEqual(self.nadim.role, "owner")
        self.assertTrue(self.nadim.is_active)

    def test_8_2_a_patient_cannot_be_edited_here(self):
        address = f"/staff/{self.samira.pk}/edit/"
        self.assertEqual(self.client.get(address).status_code, 404)

    def test_8_2_someone_who_is_not_the_owner_cannot_edit_staff(self):
        self.client.force_login(self.rana)
        response = self.edit_karim({"role": "pharmacist", "is_active": "on"})
        self.assertEqual(response.status_code, 403)
        self.karim.refresh_from_db()
        self.assertEqual(self.karim.role, "assistant")

    def test_8_2_a_disabled_account_cannot_sign_in(self):
        self.edit_karim({"role": "assistant"})
        self.client.logout()
        response = self.client.post("/login/staff/", {"username": "karim", "password": "pass"})
        self.assertContains(response, "Wrong username or password.")

    def test_8_2_disabling_an_account_ends_its_session(self):
        karims_browser = self.client_class()
        karims_browser.force_login(self.karim)
        self.edit_karim({"role": "assistant"})
        self.assertNotContains(karims_browser.get("/login/staff/"), "You are signed in as")


class StaffMenuBarTests(StaffPagesTestData):
    def setUp(self):
        self.client.force_login(self.nadim)

    def test_8_2_the_bar_shows_who_is_signed_in(self):
        response = self.client.get("/staff/")
        self.assertContains(response, "Nadim Khoury")
        self.assertContains(response, "Owner")

    def test_8_2_the_owners_name_links_to_the_staff_page(self):
        self.assertContains(self.client.get("/staff/add/"), 'href="/staff/"')

    def test_8_2_the_bar_has_a_sign_out_button(self):
        self.assertContains(self.client.get("/staff/"), 'action="/logout/"')

    def test_8_2_signing_out_returns_to_the_login_page(self):
        self.assertRedirects(self.client.post("/logout/"), "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_8_2_opening_the_sign_out_address_does_not_sign_out(self):
        self.client.get("/logout/")
        self.assertIn("_auth_user_id", self.client.session)
