from datetime import datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404
from django.template.defaultfilters import date
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.models import CaregiverLink
from accounts.permissions import can_act_as_pharmacist, can_add_medicines, is_owner, is_pharmacy_staff
from accounts.services import (
    add_caregiver,
    caregiver_links_of,
    create_patient_login,
    landing_page,
    revoke_caregiver,
)
from core.models import ActivityLog
from core.services import log_action, notify
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from passport.views import selected_patient
from prescriptions.models import Prescription, PrescriptionItem
from reservations.models import RefillRequest, Reservation

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
        self.assertRedirects(response, "/home/")
        self.assert_signed_in_as(self.samira)

    def test_8_1_pharmacist_signs_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "rana", "password": "pass"})
        self.assertRedirects(response, "/dashboard/")
        self.assert_signed_in_as(self.rana)

    def test_8_1_assistant_signs_in_on_the_staff_page(self):
        response = self.client.post("/login/staff/", {"username": "karim", "password": "pass"})
        self.assertRedirects(response, "/dashboard/")
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

    def test_p2_each_role_has_its_landing_page(self):
        User = get_user_model()
        self.assertEqual(landing_page(User(role="owner")), "/staff/")
        self.assertEqual(landing_page(User(role="pharmacist")), "/dashboard/")
        self.assertEqual(landing_page(User(role="assistant")), "/dashboard/")
        self.assertEqual(landing_page(User(role="patient")), "/home/")

    def test_p2_signing_in_returns_to_the_page_the_visitor_was_going_to(self):
        response = self.client.post("/login/?next=/reservations/find/", {"username": "samira", "password": "pass"})
        self.assertRedirects(response, "/reservations/find/")

    def test_p2_a_search_survives_signing_in(self):
        address = "/reservations/find/results/?name=Metformin&quantity=1"
        response = self.client.post(
            "/login/?" + urlencode({"next": address}), {"username": "samira", "password": "pass"}
        )
        self.assertRedirects(response, address)

    def test_p2_staff_return_to_the_page_they_were_going_to(self):
        response = self.client.post("/login/staff/?next=/inventory/", {"username": "rana", "password": "pass"})
        self.assertRedirects(response, "/inventory/")

    def test_p2_an_address_on_another_site_is_ignored(self):
        response = self.client.post(
            "/login/?next=https://example.com/", {"username": "samira", "password": "pass"}
        )
        self.assertRedirects(response, "/home/")

    def test_p2_a_refused_sign_in_keeps_the_address_to_return_to(self):
        response = self.client.post("/login/?next=/reservations/find/", {"username": "samira", "password": "wrong"})
        self.assertContains(response, "Wrong username or password.")
        self.assert_nobody_is_signed_in()


class MainPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira = User.objects.create_user(username="samira", password="pass", role="patient")
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")
        cls.karim = User.objects.create_user(username="karim", password="pass", role="assistant")
        cls.nadim = User.objects.create_user(username="nadim", password="pass", role="owner")

    def test_p2_the_site_opens_on_the_main_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/main.html")

    def test_p2_the_main_page_uses_the_figma_wording(self):
        response = self.client.get("/")
        self.assertContains(response, "Find Your Medicine. Stay Connected")
        self.assertContains(
            response,
            "Search for medicines, reserve them, track your prescriptions, request refills, "
            "and stay connected with your pharmacy, all in one place.",
        )
        for card in [
            "Find a Medicine",
            "Reserve Medicine",
            "Track Prescription",
            "Medication Passport",
            "Request a Refill",
            "AI Pharmacy Assistant",
        ]:
            self.assertContains(response, card)

    def test_p2_the_main_page_shows_the_pharmacy_details(self):
        response = self.client.get("/")
        self.assertContains(response, "Your pharmacy")
        for detail in [settings.PHARMACY_NAME, settings.PHARMACY_ADDRESS, settings.PHARMACY_PHONE, settings.PHARMACY_HOURS]:
            self.assertContains(response, detail)

    def test_p2_the_login_button_opens_the_patient_sign_in(self):
        self.assertContains(self.client.get("/"), '<a href="/login/" class="secondary-button main-login">Login</a>', html=True)

    def test_p2_sign_up_and_request_a_demo_are_left_out(self):
        response = self.client.get("/")
        self.assertNotContains(response, "Sign up")
        self.assertNotContains(response, "Request a Demo")

    def test_p2_the_menu_shows_the_public_sections(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/" class="active"')
        self.assertContains(response, 'href="/reservations/find/"')
        self.assertContains(response, 'href="/assistant/"')
        self.assertContains(response, "Medication Passport")
        self.assertNotContains(response, "Family Access")

    def test_p2_nothing_personal_is_shown_to_a_visitor(self):
        response = self.client.get("/")
        self.assertNotContains(response, "Welcome,")
        self.assertNotContains(response, 'action="/logout/"')
        self.assertNotContains(response, 'href="/notifications/"')
        self.assertNotContains(response, 'id="refill-reminders"')

    def test_p2_the_search_box_searches_the_pharmacys_stock(self):
        response = self.client.get("/")
        self.assertContains(response, 'action="/reservations/find/results/"')
        self.assertContains(response, 'placeholder="Search for a medicine"')
        self.assertContains(response, '<input type="hidden" name="quantity" value="1">', html=True)

    def test_p2_the_cards_lead_to_the_patient_pages(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/reservations/find/" class="home-card find"')
        self.assertContains(response, 'href="/prescriptions/mine/" class="home-card track"')
        self.assertContains(response, 'href="/home/#refill-reminders" class="home-card refill"')
        self.assertContains(response, 'href="/assistant/" class="home-card assistant"')

    def test_p2_a_visitor_who_searches_is_asked_to_sign_in_first(self):
        address = "/reservations/find/results/?name=Metformin&quantity=1"
        response = self.client.get(address)
        self.assertRedirects(response, "/login/?" + urlencode({"next": address}, safe="/"))

    def test_p2_signed_in_users_go_to_their_own_page(self):
        for user, page in [(self.samira, "/home/"), (self.rana, "/dashboard/"), (self.karim, "/dashboard/"), (self.nadim, "/staff/")]:
            self.client.force_login(user)
            self.assertRedirects(self.client.get("/"), page)


class PatientHomePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira = User.objects.create_user(username="samira", password="pass", role="patient")
        Patient.objects.create(name="Samira Khalil", date_of_birth="1958-03-14", user=cls.samira)
        maya = User.objects.create_user(username="maya", password="pass", role="patient")
        Patient.objects.create(name="Maya Farah", date_of_birth="1990-06-02", user=maya)
        cls.omar = User.objects.create_user(
            username="omar", password="pass", role="patient", first_name="Omar", last_name="Nassar"
        )
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")

    def home_page_for(self, user):
        self.client.force_login(user)
        return self.client.get("/home/")

    def test_p1_a_caregiver_also_lands_on_the_home_page(self):
        response = self.client.post("/login/", {"username": "omar", "password": "pass"})
        self.assertRedirects(response, "/home/")

    def test_p1_signed_out_visitors_go_to_the_patient_sign_in(self):
        self.assertRedirects(self.client.get("/home/"), "/login/?next=/home/")

    def test_p1_staff_cannot_open_the_patient_home(self):
        self.assertEqual(self.home_page_for(self.rana).status_code, 403)

    def test_p1_the_home_page_greets_the_patient_by_name(self):
        self.assertContains(self.home_page_for(self.samira), "Welcome, Samira Khalil")

    def test_p1_a_caregiver_with_no_patient_record_is_greeted_by_name(self):
        self.assertContains(self.home_page_for(self.omar), "Welcome, Omar Nassar")

    def test_p1_no_other_patient_appears(self):
        self.assertNotContains(self.home_page_for(self.samira), "Maya Farah")

    def test_p1_the_home_page_shows_the_pharmacy_details(self):
        response = self.home_page_for(self.samira)
        self.assertContains(response, "Your pharmacy")
        for detail in [settings.PHARMACY_NAME, settings.PHARMACY_ADDRESS, settings.PHARMACY_PHONE, settings.PHARMACY_HOURS]:
            self.assertContains(response, detail)

    def test_p1_the_home_page_uses_the_figma_wording(self):
        response = self.home_page_for(self.samira)
        self.assertContains(response, "Find Your Medicine. Stay Connected")
        for card in [
            "Find a Medicine",
            "Reserve Medicine",
            "Track Prescription",
            "Medication Passport",
            "Request a Refill",
            "AI Pharmacy Assistant",
        ]:
            self.assertContains(response, card)

    def test_p1_the_patient_menu_lists_the_patient_sections(self):
        response = self.home_page_for(self.samira)
        for section in ["Home", "Find Medicine", "Medication Passport", "AI Assistant", "Family Access"]:
            self.assertContains(response, section)

    def test_p1_home_is_the_selected_menu_link(self):
        self.assertContains(self.home_page_for(self.samira), 'href="/home/" class="active"')

    def test_p1_the_staff_menu_is_not_shown(self):
        response = self.home_page_for(self.samira)
        self.assertNotContains(response, 'href="/inventory/"')
        self.assertNotContains(response, 'href="/dashboard/"')

    def test_p1_the_page_shows_the_users_initials_and_a_sign_out_button(self):
        response = self.home_page_for(self.omar)
        self.assertContains(response, ">ON<")
        self.assertContains(response, 'action="/logout/"')

    def test_9_2_the_track_prescription_card_opens_the_patients_prescriptions(self):
        self.assertContains(self.home_page_for(self.samira), 'href="/prescriptions/mine/"')

    def test_9_2_the_bell_opens_the_notifications(self):
        self.assertContains(self.home_page_for(self.samira), 'href="/notifications/"')

    def test_9_2_the_bell_has_a_dot_when_something_is_unread(self):
        notify(self.samira, "Ready", "/prescriptions/mine/")
        self.assertContains(self.home_page_for(self.samira), 'class="patient-bell unread"')

    def test_9_2_the_bell_has_no_dot_when_everything_is_read(self):
        response = self.home_page_for(self.samira)
        self.assertContains(response, 'class="patient-bell"')
        self.assertNotContains(response, "patient-bell unread")


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


class FamilyTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira_user = User.objects.create_user(
            username="samira", password="pass", role="patient", first_name="Samira", last_name="Khalil"
        )
        cls.samira = Patient.objects.create(name="Samira Khalil", date_of_birth="1958-03-14", user=cls.samira_user)
        cls.maya_user = User.objects.create_user(username="maya", password="pass", role="patient")
        cls.maya = Patient.objects.create(name="Maya Farah", date_of_birth="1990-06-02", user=cls.maya_user)
        cls.omar = User.objects.create_user(
            username="omar", password="pass", role="patient", first_name="Omar", last_name="Nassar"
        )
        cls.father = Patient.objects.create(name="Khalil Nassar", date_of_birth="1949-11-05")
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")


class CaregiverLinkTests(FamilyTestData):
    def test_12_1_a_new_link_is_active(self):
        link = CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.assertTrue(link.active)

    def test_12_1_a_patient_gives_a_family_member_access_by_username(self):
        link = add_caregiver(self.samira, "omar")
        self.assertEqual((link.patient, link.caregiver, link.active), (self.samira, self.omar, True))

    def test_12_1_an_unknown_username_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "No patient account has the username nobody."):
            add_caregiver(self.samira, "nobody")
        self.assertFalse(CaregiverLink.objects.exists())

    def test_12_1_a_staff_username_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "No patient account has the username rana."):
            add_caregiver(self.samira, "rana")
        self.assertFalse(CaregiverLink.objects.exists())

    def test_12_1_a_patient_cannot_add_themselves(self):
        with self.assertRaisesMessage(ValidationError, "You cannot add yourself."):
            add_caregiver(self.samira, "samira")
        self.assertFalse(CaregiverLink.objects.exists())

    def test_12_1_a_family_member_who_has_access_is_not_added_twice(self):
        add_caregiver(self.samira, "omar")
        with self.assertRaisesMessage(ValidationError, "omar can already manage your medicines."):
            add_caregiver(self.samira, "omar")
        self.assertEqual(CaregiverLink.objects.count(), 1)

    def test_12_1_removing_access_ends_the_link(self):
        link = add_caregiver(self.samira, "omar")
        revoke_caregiver(self.samira, link.pk)
        link.refresh_from_db()
        self.assertFalse(link.active)

    def test_12_1_adding_someone_again_after_removing_them_reuses_their_link(self):
        link = add_caregiver(self.samira, "omar")
        revoke_caregiver(self.samira, link.pk)
        self.assertEqual(add_caregiver(self.samira, "omar"), link)
        link.refresh_from_db()
        self.assertTrue(link.active)
        self.assertEqual(CaregiverLink.objects.count(), 1)

    def test_12_1_only_family_members_with_access_are_listed(self):
        omar_link = add_caregiver(self.samira, "omar")
        maya_link = add_caregiver(self.samira, "maya")
        revoke_caregiver(self.samira, maya_link.pk)
        self.assertEqual(list(caregiver_links_of(self.samira)), [omar_link])

    def test_12_1_a_patient_cannot_remove_another_patients_family_member(self):
        link = CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        with self.assertRaises(Http404):
            revoke_caregiver(self.samira, link.pk)
        link.refresh_from_db()
        self.assertTrue(link.active)


class SelectedPatientTests(FamilyTestData):
    def request_for(self, user, chosen_patient=None):
        request = RequestFactory().get("/home/")
        request.user = user
        request.session = {}
        if chosen_patient:
            request.session["viewing_patient_id"] = chosen_patient.pk
        return request

    def test_12_1_a_patient_views_their_own_record_by_default(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user)
        self.assertEqual(selected_patient(self.request_for(self.maya_user)), self.maya)

    def test_12_1_a_caregiver_with_no_record_views_the_patient_they_care_for(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.assertEqual(selected_patient(self.request_for(self.omar)), self.father)

    def test_12_1_the_chosen_patient_is_viewed(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user)
        self.assertEqual(selected_patient(self.request_for(self.maya_user, self.father)), self.father)

    def test_12_1_a_patient_the_user_does_not_care_for_cannot_be_chosen(self):
        self.assertEqual(selected_patient(self.request_for(self.maya_user, self.samira)), self.maya)

    def test_12_1_after_access_is_removed_the_patient_is_no_longer_viewed(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user, active=False)
        self.assertEqual(selected_patient(self.request_for(self.maya_user, self.father)), self.maya)

    def test_12_1_a_user_with_no_record_and_no_links_views_nobody(self):
        self.assertIsNone(selected_patient(self.request_for(self.omar)))


class FamilyAccessPageTests(FamilyTestData):
    def page_for(self, user, address="/family/"):
        self.client.force_login(user)
        return self.client.get(address)

    def shown_messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_12_1_signed_out_visitors_go_to_the_patient_sign_in(self):
        self.assertRedirects(self.client.get("/family/"), "/login/?next=/family/")

    def test_12_1_staff_cannot_open_family_access(self):
        self.assertEqual(self.page_for(self.rana).status_code, 403)

    def test_12_1_the_menu_links_to_family_access(self):
        self.assertContains(self.page_for(self.samira_user, "/home/"), 'href="/family/"')
        self.assertContains(self.page_for(self.samira_user), 'href="/family/" class="active"')

    def test_12_1_the_page_uses_the_figma_wording(self):
        response = self.page_for(self.samira_user)
        self.assertContains(response, "Family care")
        self.assertContains(response, "Caregiver Access")
        self.assertContains(response, "Manage a family member’s medicines, reminders, and pickup status from your account.")
        self.assertContains(response, "Linked Family Profiles")
        self.assertContains(response, "Permission &amp; Access")
        for permission in ["View and manage medicines", "Manage reminders", "View pickup status", "Access pharmacy details"]:
            self.assertContains(response, permission)

    def test_12_1_a_patient_with_no_family_member_is_told_so(self):
        self.assertContains(self.page_for(self.samira_user), "No family member has access yet.")

    def test_12_1_a_patient_sees_who_has_access_with_a_remove_button(self):
        link = add_caregiver(self.samira, "omar")
        response = self.page_for(self.samira_user)
        self.assertContains(response, "Omar Nassar")
        self.assertContains(response, f'action="/family/{link.pk}/remove/"')
        self.assertContains(response, "Remove Access")

    def test_12_1_a_caregiver_sees_the_patient_they_care_for(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        response = self.page_for(self.omar)
        self.assertContains(response, "Khalil Nassar")
        self.assertContains(response, "Managed by you")
        self.assertContains(response, "Managing 1 family member")

    def test_12_1_a_caregiver_sees_that_their_access_is_active(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        response = self.page_for(self.omar)
        self.assertContains(response, "Authorized caregiver access active")
        self.assertContains(
            response, "You can manage Khalil Nassar’s medicines, reminders, and pickup status from your account."
        )

    def test_12_1_patients_with_a_record_see_add_family_member(self):
        self.assertContains(self.page_for(self.samira_user), 'href="/family/add/"')

    def test_12_1_a_caregiver_with_no_record_has_nothing_to_share(self):
        self.assertNotContains(self.page_for(self.omar), 'href="/family/add/"')
        self.assertEqual(self.page_for(self.omar, "/family/add/").status_code, 403)

    def test_12_1_the_add_popup_has_its_wording(self):
        response = self.page_for(self.samira_user, "/family/add/")
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "Add Family Member")
        self.assertContains(response, "Their CarePharm username")
        self.assertContains(response, "Give Access")
        self.assertContains(response, "Caregiver Access")

    def test_12_1_giving_access_links_the_family_member(self):
        self.client.force_login(self.samira_user)
        response = self.client.post("/family/add/", {"username": "omar"}, follow=True)
        self.assertRedirects(response, "/family/")
        self.assertTrue(CaregiverLink.objects.filter(patient=self.samira, caregiver=self.omar, active=True).exists())
        self.assertEqual(self.shown_messages(response), ["Omar Nassar can now manage your medicines."])

    def test_12_1_an_unknown_username_keeps_the_popup_open_with_the_reason(self):
        self.client.force_login(self.samira_user)
        response = self.client.post("/family/add/", {"username": "nobody"})
        self.assertContains(response, "<dialog open")
        self.assertContains(response, "No patient account has the username nobody.")
        self.assertFalse(CaregiverLink.objects.exists())

    def test_12_1_a_username_is_required(self):
        self.client.force_login(self.samira_user)
        response = self.client.post("/family/add/", {"username": ""})
        self.assertContains(response, "<dialog open")
        self.assertFalse(CaregiverLink.objects.exists())

    def test_12_1_removing_access_ends_it_at_once(self):
        link = add_caregiver(self.samira, "omar")
        self.client.force_login(self.samira_user)
        response = self.client.post(f"/family/{link.pk}/remove/", follow=True)
        self.assertRedirects(response, "/family/")
        link.refresh_from_db()
        self.assertFalse(link.active)
        self.assertEqual(self.shown_messages(response), ["Omar Nassar can no longer manage your medicines."])

    def test_12_1_opening_the_remove_address_removes_nothing(self):
        link = add_caregiver(self.samira, "omar")
        self.assertRedirects(self.page_for(self.samira_user, f"/family/{link.pk}/remove/"), "/family/")
        link.refresh_from_db()
        self.assertTrue(link.active)

    def test_12_1_a_caregiver_cannot_remove_their_own_access_to_someone_else(self):
        link = CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.client.force_login(self.samira_user)
        self.assertEqual(self.client.post(f"/family/{link.pk}/remove/").status_code, 404)
        link.refresh_from_db()
        self.assertTrue(link.active)


def collected_days_ago(patient, medicine, days):
    collected_on = timezone.localdate() - timedelta(days=days)
    prescription = Prescription.objects.create(
        patient=patient,
        status="collected",
        collected_at=timezone.make_aware(datetime.combine(collected_on, time(12, 0))),
    )
    return PrescriptionItem.objects.create(
        prescription=prescription, medicine=medicine, quantity=60, days_of_supply=30, long_term=True
    )


class ViewingAsTests(FamilyTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        drug_class = DrugClass.objects.create(name="Biguanides")
        cls.metformin = Medicine.objects.create(
            name="Metformin",
            strength="500 mg",
            barcode="1000000000004",
            drug_class=drug_class,
            price=Decimal("6.00"),
            minimum_quantity=5,
        )
        cls.due_item = collected_days_ago(cls.father, cls.metformin, 27)
        cls.ready = Prescription.objects.create(patient=cls.father, status="ready", ready_at=timezone.now())
        PrescriptionItem.objects.create(prescription=cls.ready, medicine=cls.metformin, quantity=30, days_of_supply=30)

    def setUp(self):
        self.omar_link = CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        Batch.objects.create(
            medicine=self.metformin,
            batch_number="MET-001",
            expiry_date=timezone.localdate() + timedelta(days=300),
            quantity=50,
            unit_cost=Decimal("3.00"),
            supplier="Cedar Medical Supplies",
        )

    def view_as(self, user, patient, next_address="/home/"):
        self.client.force_login(user)
        return self.client.post(f"/family/view/{patient.pk}/", {"next": next_address})

    def refill_address(self):
        return f"/reservations/refills/request/{self.due_item.pk}/"

    def test_12_1_a_user_with_family_links_gets_a_switcher(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user)
        self.client.force_login(self.maya_user)
        response = self.client.get("/home/")
        self.assertContains(response, "Maya Farah (me)")
        self.assertContains(response, f'action="/family/view/{self.maya.pk}/"')
        self.assertContains(response, f'action="/family/view/{self.father.pk}/"')

    def test_12_1_a_user_with_no_family_links_gets_no_switcher(self):
        self.client.force_login(self.samira_user)
        self.assertNotContains(self.client.get("/home/"), 'action="/family/view/')

    def test_12_1_the_viewed_patient_is_marked_in_the_switcher(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user)
        self.view_as(self.maya_user, self.father)
        response = self.client.get("/home/")
        self.assertEqual(response.context["viewing"], self.father)
        self.assertContains(response, "profile-pill selected")

    def test_12_1_switching_returns_to_the_same_page(self):
        response = self.view_as(self.omar, self.father, "/prescriptions/mine/")
        self.assertRedirects(response, "/prescriptions/mine/")
        self.assertEqual(self.client.session["viewing_patient_id"], self.father.pk)

    def test_12_1_switching_never_leaves_the_site(self):
        self.assertRedirects(self.view_as(self.omar, self.father, "https://example.com/"), "/home/")

    def test_12_1_a_patient_the_user_does_not_care_for_cannot_be_viewed(self):
        response = self.view_as(self.omar, self.samira)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("viewing_patient_id", self.client.session)

    def test_12_1_opening_the_switch_address_changes_nothing(self):
        self.client.force_login(self.omar)
        self.assertRedirects(self.client.get(f"/family/view/{self.father.pk}/"), "/home/")
        self.assertNotIn("viewing_patient_id", self.client.session)

    def test_12_1_staff_cannot_switch(self):
        self.assertEqual(self.view_as(self.rana, self.father).status_code, 403)

    def test_12_1_the_home_page_shows_the_viewed_patients_refill_reminders(self):
        self.client.force_login(self.omar)
        response = self.client.get("/home/")
        self.assertContains(response, "Metformin 500 mg will run out in 3 days.")
        self.assertContains(response, f'action="{self.refill_address()}"')

    def test_12_1_a_patient_sees_their_own_reminders_until_they_switch(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.maya_user)
        self.client.force_login(self.maya_user)
        self.assertNotContains(self.client.get("/home/"), f'action="{self.refill_address()}"')
        self.view_as(self.maya_user, self.father)
        self.assertContains(self.client.get("/home/"), f'action="{self.refill_address()}"')

    def test_12_1_a_caregiver_requests_a_refill_for_the_viewed_patient(self):
        self.client.force_login(self.omar)
        self.client.post(self.refill_address())
        refill_request = RefillRequest.objects.get()
        self.assertEqual((refill_request.prescription_item, refill_request.requested_by), (self.due_item, self.omar))

    def test_12_1_a_caregiver_tracks_the_viewed_patients_prescriptions(self):
        self.client.force_login(self.omar)
        response = self.client.get("/prescriptions/mine/")
        self.assertIn(self.ready, [row["prescription"] for row in response.context["rows"]])

    def test_12_1_a_caregiver_can_reserve_for_the_viewed_patient(self):
        self.client.force_login(self.omar)
        results = self.client.get("/reservations/find/results/", {"name": "Metformin", "quantity": 1})
        self.assertTrue(results.context["can_reserve"])
        response = self.client.post("/reservations/reserve/", {"medicine": self.metformin.pk, "quantity": 1})
        reservation = Reservation.objects.get()
        self.assertEqual((reservation.patient, reservation.medicine), (self.father, self.metformin))
        self.assertRedirects(response, f"/reservations/{reservation.pk}/")

    def test_12_1_the_viewed_patients_reservation_page_opens(self):
        self.client.force_login(self.omar)
        self.client.post("/reservations/reserve/", {"medicine": self.metformin.pk, "quantity": 1})
        reservation = Reservation.objects.get()
        self.assertEqual(self.client.get(f"/reservations/{reservation.pk}/").status_code, 200)

    def test_12_1_after_access_is_removed_the_caregiver_sees_nothing_of_the_patient(self):
        revoke_caregiver(self.father, self.omar_link.pk)
        self.client.force_login(self.omar)
        self.assertNotContains(self.client.get("/home/"), f'action="{self.refill_address()}"')
        self.assertEqual(self.client.post(self.refill_address()).status_code, 404)
        self.assertEqual(self.view_as(self.omar, self.father).status_code, 404)

    def test_12_1_the_header_still_shows_the_signed_in_user(self):
        self.client.force_login(self.omar)
        response = self.client.get("/home/")
        self.assertContains(response, "Welcome, Omar Nassar")
        self.assertContains(response, ">ON<")


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

    def test_f1_owners_and_pharmacists_can_add_medicines(self):
        self.assertTrue(can_add_medicines(get_user_model()(role="owner")))
        self.assertTrue(can_add_medicines(get_user_model()(role="pharmacist")))

    def test_f1_assistants_and_patients_cannot_add_medicines(self):
        self.assertFalse(can_add_medicines(get_user_model()(role="assistant")))
        self.assertFalse(can_add_medicines(get_user_model()(role="patient")))


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

    def test_f1_the_role_permissions_include_adding_medicines(self):
        self.assertContains(self.client.get("/staff/"), "Add new medicines")

    def test_f2_the_role_permissions_include_changing_prices(self):
        self.assertContains(self.client.get("/staff/"), "Change prices")


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
        self.assertRedirects(response, "/dashboard/")

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

    def test_f5_a_common_staff_password_is_refused(self):
        response = self.client.post("/staff/add/", {**NEW_STAFF, "password": "password"})
        self.assertContains(response, "This password is too common.")
        self.assert_nobody_was_added()

    def test_f5_an_all_digits_staff_password_is_refused(self):
        response = self.client.post("/staff/add/", {**NEW_STAFF, "password": "12345678"})
        self.assertContains(response, "This password is entirely numeric.")
        self.assert_nobody_was_added()

    def test_f5_a_staff_password_like_the_username_is_refused(self):
        response = self.client.post(
            "/staff/add/", {**NEW_STAFF, "username": "halanader", "password": "halanader1"}
        )
        self.assertContains(response, "The password is too similar to the username.")
        self.assertFalse(get_user_model().objects.filter(username="halanader").exists())

    def test_f5_a_staff_username_with_a_space_is_refused(self):
        response = self.client.post("/staff/add/", {**NEW_STAFF, "username": "hala nader"})
        self.assertContains(response, "Enter a valid username.")
        self.assertFalse(get_user_model().objects.filter(username="hala nader").exists())


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


class ActivityLogCardTests(StaffPagesTestData):
    def setUp(self):
        self.client.force_login(self.nadim)

    def log_yesterday(self, user, action, details=""):
        entry = log_action(user, action, details)
        ActivityLog.objects.filter(pk=entry.pk).update(time=timezone.now() - timedelta(days=1))
        entry.refresh_from_db()
        return entry

    def test_8_3_the_staff_page_has_the_activity_log_card(self):
        response = self.client.get("/staff/")
        self.assertContains(response, "Activity Log")
        self.assertContains(response, "Every sale, stock change and safety decision, with who made it")

    def test_8_3_the_page_intro_mentions_who_did_what(self):
        self.assertContains(
            self.client.get("/staff/"),
            "Who can use CarePharm, what each role is allowed to do, and who did what.",
        )

    def test_8_3_an_entry_shows_the_action_and_its_details(self):
        log_action(self.karim, "Recorded delivery", "Amlodipine 5 mg · batch AML-002 · +60")
        response = self.client.get("/staff/")
        self.assertContains(response, "Recorded delivery")
        self.assertContains(response, "Amlodipine 5 mg · batch AML-002 · +60")

    def test_8_3_an_entry_shows_who_did_it_and_their_role(self):
        log_action(self.karim, "Recorded delivery")
        response = self.client.get("/staff/")
        self.assertContains(response, "<td><strong>Karim Saad</strong><small>Assistant</small></td>", html=True)

    def test_8_3_an_entry_made_by_the_system_says_so(self):
        log_action(None, "Reservation expired")
        self.assertContains(self.client.get("/staff/"), "<td><strong>System</strong></td>", html=True)

    def test_8_3_today_shows_the_time_of_each_entry(self):
        entry = log_action(self.karim, "Recorded delivery")
        self.assertContains(self.client.get("/staff/"), date(timezone.localtime(entry.time), "g:i A"))

    def test_8_3_today_leaves_out_earlier_days(self):
        self.log_yesterday(self.rana, "Returned stock")
        response = self.client.get("/staff/")
        self.assertNotContains(response, "Returned stock")
        self.assertContains(response, "No activity today.")

    def test_8_3_all_shows_earlier_days_with_their_date(self):
        entry = self.log_yesterday(self.rana, "Returned stock")
        response = self.client.get("/staff/?show=all")
        self.assertContains(response, "Returned stock")
        self.assertContains(response, date(timezone.localtime(entry.time), "j M Y, g:i A"))

    def test_8_3_all_with_an_empty_log_says_so(self):
        self.assertContains(self.client.get("/staff/?show=all"), "No activity yet.")

    def test_8_3_the_card_links_to_the_whole_log(self):
        self.assertContains(self.client.get("/staff/"), 'href="/staff/?show=all"')

    def test_2_2_safety_decisions_stand_out_in_the_log(self):
        log_action(self.rana, "Safety decision", "Ampicillin 250 mg for Ahmad")
        log_action(self.karim, "Recorded delivery")
        response = self.client.get("/staff/")
        self.assertContains(response, '<td class="log-action safety-decision">Safety decision</td>', html=True)
        self.assertContains(response, '<td class="log-action">Recorded delivery</td>', html=True)
