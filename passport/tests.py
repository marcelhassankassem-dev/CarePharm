from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from accounts.models import CaregiverLink
from inventory.models import DrugClass, Medicine
from passport.forms import PatientChoiceForm
from passport.models import Allergy, CurrentMedicine, PassportShare, Patient
from core.models import Notification
from core.services import notify
from passport.services import (
    active_passport_share,
    add_current_medicine,
    create_patient,
    find_patients,
    patient_header,
    patient_name_for,
    patient_of,
    patients_cared_for,
    viewable_patients,
)
from sales.models import Sale, SaleItem

STAFF_PAGES = ["/patients/", "/patients/new/", "/patients/logins/new/"]


def make_medicine(name, barcode, drug_class):
    return Medicine.objects.create(
        name=name,
        strength="500 mg",
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal("5.00"),
        minimum_quantity=10,
    )


def make_user(username, role):
    return get_user_model().objects.create(username=username, role=role)


class PatientModelTests(TestCase):
    def test_t3_patient_is_saved_with_name_and_date_of_birth(self):
        Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        samira = Patient.objects.get(name="Samira")
        self.assertEqual(samira.date_of_birth, date(1958, 3, 14))

    def test_t3_phone_is_optional(self):
        ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))
        self.assertEqual(ahmad.phone, "")

    def test_t3_patient_can_have_no_user(self):
        ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))
        self.assertIsNone(ahmad.user)

    def test_t3_patient_can_be_linked_to_a_user(self):
        user = get_user_model().objects.create_user(username="maya", password="pass")
        maya = Patient.objects.create(name="Maya", date_of_birth=date(1995, 9, 21), user=user)
        self.assertEqual(maya.user, user)

    def test_t3_user_can_have_only_one_patient_record(self):
        user = get_user_model().objects.create_user(username="maya", password="pass")
        Patient.objects.create(name="Maya", date_of_birth=date(1995, 9, 21), user=user)
        with self.assertRaises(IntegrityError):
            Patient.objects.create(name="Another", date_of_birth=date(1990, 1, 1), user=user)

    def test_t3_deleting_a_user_keeps_the_patient(self):
        user = get_user_model().objects.create_user(username="maya", password="pass")
        maya = Patient.objects.create(name="Maya", date_of_birth=date(1995, 9, 21), user=user)
        user.delete()
        maya.refresh_from_db()
        self.assertIsNone(maya.user)


class AllergyAndCurrentMedicineModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.penicillins = DrugClass.objects.create(name="Penicillins")
        cls.statins = DrugClass.objects.create(name="Statins")
        cls.atorvastatin = make_medicine("Atorvastatin", "1", cls.statins)

    def test_3_1_allergy_links_a_patient_to_a_drug_class(self):
        allergy = Allergy.objects.create(patient=self.samira, drug_class=self.penicillins)
        self.assertEqual(allergy.patient, self.samira)
        self.assertEqual(allergy.drug_class, self.penicillins)

    def test_3_1_current_medicine_links_a_patient_to_a_medicine(self):
        current = CurrentMedicine.objects.create(patient=self.samira, medicine=self.atorvastatin)
        self.assertEqual(current.patient, self.samira)
        self.assertEqual(current.medicine, self.atorvastatin)

    def test_3_1_a_drug_class_someone_is_allergic_to_cannot_be_deleted(self):
        Allergy.objects.create(patient=self.samira, drug_class=self.penicillins)
        with self.assertRaises(ProtectedError):
            self.penicillins.delete()

    def test_3_1_a_medicine_someone_takes_cannot_be_deleted(self):
        CurrentMedicine.objects.create(patient=self.samira, medicine=self.atorvastatin)
        with self.assertRaises(ProtectedError):
            self.atorvastatin.delete()


class CreatePatientServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.penicillins = DrugClass.objects.create(name="Penicillins")
        cls.statins = DrugClass.objects.create(name="Statins")
        cls.atorvastatin = make_medicine("Atorvastatin", "1", cls.statins)

    def test_3_1_create_patient_saves_the_contact_details(self):
        samira = create_patient("Samira", date(1958, 3, 14), phone="03 100 001")
        samira.refresh_from_db()
        self.assertEqual(samira.name, "Samira")
        self.assertEqual(samira.date_of_birth, date(1958, 3, 14))
        self.assertEqual(samira.phone, "03 100 001")

    def test_3_1_create_patient_saves_the_allergies(self):
        samira = create_patient("Samira", date(1958, 3, 14), drug_classes=[self.penicillins])
        allergy = Allergy.objects.get(patient=samira)
        self.assertEqual(allergy.drug_class, self.penicillins)

    def test_3_1_create_patient_saves_the_current_medicines(self):
        samira = create_patient("Samira", date(1958, 3, 14), medicines=[self.atorvastatin])
        current = CurrentMedicine.objects.get(patient=samira)
        self.assertEqual(current.medicine, self.atorvastatin)

    def test_3_1_phone_allergies_and_medicines_are_optional(self):
        ahmad = create_patient("Ahmad", date(1980, 6, 2))
        self.assertEqual(ahmad.phone, "")
        self.assertFalse(Allergy.objects.filter(patient=ahmad).exists())
        self.assertFalse(CurrentMedicine.objects.filter(patient=ahmad).exists())

    def test_3_1_a_patient_has_no_login_unless_one_is_asked_for(self):
        ahmad = create_patient("Ahmad", date(1980, 6, 2))
        self.assertIsNone(ahmad.user)

    def test_3_1_create_patient_can_also_create_their_login(self):
        samira = create_patient(
            "Samira", date(1958, 3, 14), username="samira", password="temporary1"
        )
        self.assertEqual(samira.user.username, "samira")
        self.assertEqual(samira.user.role, "patient")
        self.assertTrue(samira.user.check_password("temporary1"))

    def test_3_1_a_taken_username_saves_nothing(self):
        make_user("samira", "patient")
        with self.assertRaisesMessage(ValidationError, "That username is already taken."):
            create_patient(
                "Samira",
                date(1958, 3, 14),
                drug_classes=[self.penicillins],
                username="samira",
                password="temporary1",
            )
        self.assertFalse(Patient.objects.exists())
        self.assertFalse(Allergy.objects.exists())


class AddCurrentMedicineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.metformin = make_medicine("Metformin", "1", DrugClass.objects.create(name="Biguanides"))

    def test_1_1_a_medicine_joins_the_current_medicines(self):
        add_current_medicine(self.samira, self.metformin)
        current = CurrentMedicine.objects.get(patient=self.samira)
        self.assertEqual(current.medicine, self.metformin)

    def test_1_1_a_current_medicine_is_not_added_twice(self):
        add_current_medicine(self.samira, self.metformin)
        add_current_medicine(self.samira, self.metformin)
        self.assertEqual(CurrentMedicine.objects.filter(patient=self.samira).count(), 1)


class FindPatientsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira = Patient.objects.create(
            name="Samira", date_of_birth=date(1958, 3, 14), phone="03 100 001"
        )
        cls.maya = Patient.objects.create(
            name="Maya", date_of_birth=date(1990, 9, 21), phone="03 100 002"
        )

    def test_3_1_patients_are_listed_by_name(self):
        self.assertEqual(list(find_patients()), [self.maya, self.samira])

    def test_3_1_search_finds_part_of_a_name_in_any_case(self):
        self.assertEqual(list(find_patients("SAM")), [self.samira])

    def test_3_1_search_finds_part_of_a_phone_number(self):
        self.assertEqual(list(find_patients("100 002")), [self.maya])

    def test_3_1_search_finds_a_passport_id(self):
        self.assertEqual(list(find_patients(f"CP-{self.maya.pk:05d}")), [self.maya])

    def test_3_1_search_with_no_match_finds_nobody(self):
        self.assertEqual(list(find_patients("nobody")), [])

    def test_3_1_each_listed_patient_knows_how_many_allergies_they_have(self):
        penicillins = DrugClass.objects.create(name="Penicillins")
        Allergy.objects.create(patient=self.samira, drug_class=penicillins)
        maya, samira = find_patients()
        self.assertEqual(maya.allergy_count, 0)
        self.assertEqual(samira.allergy_count, 1)


class PatientOfUserTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira_user = make_user("samira", "patient")
        cls.samira = Patient.objects.create(name="Samira Khalil", date_of_birth=date(1958, 3, 14), user=cls.samira_user)
        Patient.objects.create(name="Maya Farah", date_of_birth=date(1990, 6, 2), user=make_user("maya", "patient"))
        cls.omar = get_user_model().objects.create(
            username="omar", role="patient", first_name="Omar", last_name="Nassar"
        )

    def test_p1_a_patient_side_user_has_their_own_patient_record(self):
        self.assertEqual(patient_of(self.samira_user), self.samira)

    def test_p1_a_caregiver_with_no_record_has_no_patient(self):
        self.assertIsNone(patient_of(self.omar))

    def test_p1_the_name_shown_is_the_patients_name(self):
        self.assertEqual(patient_name_for(self.samira_user), "Samira Khalil")

    def test_p1_a_caregiver_is_shown_by_their_full_name(self):
        self.assertEqual(patient_name_for(self.omar), "Omar Nassar")

    def test_p1_a_user_with_no_name_is_shown_by_their_username(self):
        self.assertEqual(patient_name_for(make_user("guest", "patient")), "guest")

    def test_9_2_every_patient_page_gets_the_name_and_the_unread_count(self):
        notify(self.samira_user, "Ready", "/prescriptions/mine/")
        self.assertEqual(patient_header(self.samira_user), {"patient_name": "Samira Khalil", "unread_count": 1})


class PatientsCaredForTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira_user = make_user("samira", "patient")
        cls.samira = Patient.objects.create(name="Samira Khalil", date_of_birth=date(1958, 3, 14), user=cls.samira_user)
        cls.father = Patient.objects.create(name="Khalil Nassar", date_of_birth=date(1949, 11, 5))
        cls.maya = Patient.objects.create(name="Maya Farah", date_of_birth=date(1990, 6, 2))
        cls.omar = make_user("omar", "patient")

    def test_12_1_a_user_with_no_links_cares_for_nobody(self):
        self.assertEqual(list(patients_cared_for(self.omar)), [])

    def test_12_1_an_active_link_lets_the_caregiver_care_for_the_patient(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.assertEqual(list(patients_cared_for(self.omar)), [self.father])

    def test_12_1_a_revoked_link_no_longer_counts(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar, active=False)
        self.assertEqual(list(patients_cared_for(self.omar)), [])

    def test_12_1_patients_cared_for_are_in_name_order(self):
        CaregiverLink.objects.create(patient=self.maya, caregiver=self.omar)
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.assertEqual(list(patients_cared_for(self.omar)), [self.father, self.maya])

    def test_12_1_a_patient_views_only_their_own_record_with_no_links(self):
        self.assertEqual(viewable_patients(self.samira_user), [self.samira])

    def test_12_1_a_patient_who_is_also_a_caregiver_sees_themselves_first(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.samira_user)
        self.assertEqual(viewable_patients(self.samira_user), [self.samira, self.father])

    def test_12_1_a_caregiver_with_no_record_views_only_the_patients_they_care_for(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.omar)
        self.assertEqual(viewable_patients(self.omar), [self.father])


class PatientChoiceFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))

    def test_5_2_every_patient_is_offered_by_name(self):
        patients = list(PatientChoiceForm().fields["patient"].queryset)
        self.assertEqual(patients, [self.ahmad, self.samira])

    def test_5_2_the_list_starts_with_select_a_patient(self):
        self.assertEqual(PatientChoiceForm().fields["patient"].empty_label, "Select a patient")

    def test_5_2_a_patient_is_required(self):
        self.assertFalse(PatientChoiceForm({"patient": ""}).is_valid())

    def test_5_2_the_chosen_patient_is_given_back(self):
        form = PatientChoiceForm({"patient": self.samira.pk})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["patient"], self.samira)


class StaffOnlyPagesTests(TestCase):
    def test_3_1_signed_out_visitors_are_sent_to_the_staff_login(self):
        for page in STAFF_PAGES:
            self.assertRedirects(self.client.get(page), f"/login/staff/?next={page}")

    def test_3_1_patient_side_users_are_refused(self):
        self.client.force_login(make_user("samira", "patient"))
        for page in STAFF_PAGES:
            self.assertEqual(self.client.get(page).status_code, 403)

    def test_3_1_every_staff_role_can_open_the_pages(self):
        for role in ["pharmacist", "assistant", "owner"]:
            self.client.force_login(make_user(role, role))
            for page in STAFF_PAGES:
                self.assertEqual(self.client.get(page).status_code, 200)


class PatientsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.karim = make_user("karim", "assistant")
        cls.penicillins = DrugClass.objects.create(name="Penicillins")
        cls.statins = DrugClass.objects.create(name="Statins")
        cls.atorvastatin = make_medicine("Atorvastatin", "1", cls.statins)
        cls.maya = Patient.objects.create(
            name="Maya", date_of_birth=date(1990, 9, 21), phone="03 100 002"
        )
        cls.samira = Patient.objects.create(
            name="Samira", date_of_birth=date(1958, 3, 14), phone="03 100 001"
        )
        Allergy.objects.create(patient=cls.samira, drug_class=cls.penicillins)
        CurrentMedicine.objects.create(patient=cls.samira, medicine=cls.atorvastatin)

    def setUp(self):
        self.client.force_login(self.karim)

    def test_3_1_page_uses_the_figma_wording(self):
        response = self.client.get("/patients/")
        self.assertContains(response, "PHARMACIST CONSOLE")
        self.assertContains(response, "medication passport: contact details, allergies")
        self.assertContains(response, "Add Patient")
        self.assertContains(response, "ALL PATIENTS · 2")

    def test_3_1_the_menu_links_to_the_patients_page(self):
        self.assertContains(self.client.get("/patients/"), 'href="/patients/"')

    def test_3_1_the_menu_shows_who_is_signed_in(self):
        self.assertContains(self.client.get("/patients/"), "karim")

    def test_3_1_every_patient_is_in_the_list(self):
        response = self.client.get("/patients/")
        self.assertContains(response, f'href="/patients/{self.maya.pk}/"')
        self.assertContains(response, f'href="/patients/{self.samira.pk}/"')

    def test_3_1_the_first_patient_is_selected_when_none_is_chosen(self):
        response = self.client.get("/patients/")
        self.assertEqual(response.context["selected"], self.maya)

    def test_3_1_a_chosen_patient_shows_their_passport(self):
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertEqual(response.context["selected"], self.samira)
        self.assertContains(response, "03 100 001")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "Atorvastatin 500 mg")

    def test_staff_patient_dashboard_matches_the_three_card_layout(self):
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(response, 'class="card staff-patients-detail"')
        self.assertContains(response, "Passport Sharing")
        self.assertContains(response, "DOSE")
        self.assertContains(response, "STARTED")
        self.assertContains(response, "Not recorded")
        self.assertContains(response, f"CP-{self.samira.pk:05d}")

    def test_pharmacist_can_start_a_prescription_for_selected_patient(self):
        self.client.force_login(make_user("rina", "pharmacist"))
        patient_page = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(patient_page, f'/prescriptions/new/?patient={self.samira.pk}')
        new_prescription = self.client.get("/prescriptions/new/", {"patient": self.samira.pk})
        self.assertEqual(new_prescription.context["form"].initial["patient"], self.samira.pk)

    def test_3_1_a_patient_with_no_allergies_says_so(self):
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "No allergies recorded")
        self.assertNotContains(response, "Penicillins")

    def test_3_1_a_patient_with_no_medicines_says_so(self):
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "No active medicines recorded")
        self.assertNotContains(response, "Atorvastatin")

    def test_3_1_an_unknown_patient_is_not_found(self):
        self.assertEqual(self.client.get("/patients/99999/").status_code, 404)

    def test_3_1_search_narrows_the_list(self):
        response = self.client.get("/patients/", {"search": "sam"})
        self.assertEqual(list(response.context["patients"]), [self.samira])
        self.assertEqual(response.context["selected"], self.samira)

    def test_3_1_a_search_with_no_match_says_so(self):
        response = self.client.get("/patients/", {"search": "nobody"})
        self.assertContains(response, "No patients found.")
        self.assertIsNone(response.context["selected"])


class AddPatientPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.karim = make_user("karim", "assistant")
        cls.penicillins = DrugClass.objects.create(name="Penicillins")
        cls.statins = DrugClass.objects.create(name="Statins")
        cls.atorvastatin = make_medicine("Atorvastatin", "1", cls.statins)

    def setUp(self):
        self.client.force_login(self.karim)

    def patient_details(self):
        return {"name": "Samira", "date_of_birth": "1958-03-14", "phone": "03 100 001"}

    def test_3_1_form_offers_the_drug_classes_and_medicines(self):
        response = self.client.get("/patients/new/")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "Atorvastatin 500 mg")

    def test_3_1_adding_a_patient_opens_their_passport(self):
        response = self.client.post("/patients/new/", self.patient_details())
        samira = Patient.objects.get(name="Samira")
        self.assertRedirects(response, f"/patients/{samira.pk}/")
        self.assertEqual(samira.phone, "03 100 001")
        self.assertIsNone(samira.user)

    def test_3_1_adding_a_patient_saves_what_was_ticked(self):
        details = self.patient_details()
        details["allergies"] = [self.penicillins.pk]
        details["current_medicines"] = [self.atorvastatin.pk]
        self.client.post("/patients/new/", details)
        samira = Patient.objects.get(name="Samira")
        self.assertEqual(Allergy.objects.get(patient=samira).drug_class, self.penicillins)
        self.assertEqual(CurrentMedicine.objects.get(patient=samira).medicine, self.atorvastatin)

    def test_3_1_adding_a_patient_can_create_their_login(self):
        details = self.patient_details()
        details["username"] = "samira"
        details["password"] = "temporary1"
        self.client.post("/patients/new/", details)
        samira = Patient.objects.get(name="Samira")
        self.assertEqual(samira.user.username, "samira")
        self.assertEqual(samira.user.role, "patient")

    def test_3_1_a_name_and_date_of_birth_are_required(self):
        response = self.client.post("/patients/new/", {"phone": "03 100 001"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Patient.objects.exists())

    def test_3_1_a_username_without_a_password_is_refused(self):
        details = self.patient_details()
        details["username"] = "samira"
        response = self.client.post("/patients/new/", details)
        self.assertContains(response, "Enter both a username and a temporary password")
        self.assertFalse(Patient.objects.exists())

    def test_3_1_a_password_without_a_username_is_refused(self):
        details = self.patient_details()
        details["password"] = "temporary1"
        response = self.client.post("/patients/new/", details)
        self.assertContains(response, "Enter both a username and a temporary password")
        self.assertFalse(Patient.objects.exists())

    def test_3_1_a_taken_username_is_refused_and_saves_nothing(self):
        make_user("samira", "patient")
        details = self.patient_details()
        details["username"] = "samira"
        details["password"] = "temporary1"
        response = self.client.post("/patients/new/", details)
        self.assertContains(response, "That username is already taken.")
        self.assertFalse(Patient.objects.exists())

    def test_3_1_the_temporary_password_is_hidden_as_it_is_typed(self):
        self.assertContains(self.client.get("/patients/new/"), 'type="password"')

    def test_3_1_a_short_temporary_password_is_refused(self):
        details = self.patient_details()
        details["username"] = "samira"
        details["password"] = "short"
        response = self.client.post("/patients/new/", details)
        self.assertContains(response, "at least 8 characters")
        self.assertFalse(Patient.objects.exists())

    def test_3_1_a_short_password_is_not_reported_as_a_missing_one(self):
        details = self.patient_details()
        details["username"] = "samira"
        details["password"] = "short"
        response = self.client.post("/patients/new/", details)
        self.assertNotContains(response, "Enter both a username and a temporary password")

    def post_patient(self, **changes):
        return self.client.post("/patients/new/", {**self.patient_details(), **changes})

    def assert_refused(self, response, message):
        self.assertContains(response, message)
        self.assertFalse(Patient.objects.exists())

    def test_f5_a_date_of_birth_in_the_future_is_refused(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self.post_patient(date_of_birth=tomorrow.isoformat())
        self.assert_refused(response, "The date of birth cannot be in the future.")

    def test_f5_a_baby_born_today_can_be_added(self):
        self.post_patient(date_of_birth=timezone.localdate().isoformat())
        self.assertTrue(Patient.objects.filter(name="Samira").exists())

    def test_f5_a_date_of_birth_more_than_120_years_ago_is_refused(self):
        too_old = date(timezone.localdate().year - 121, 12, 31)
        response = self.post_patient(date_of_birth=too_old.isoformat())
        self.assert_refused(response, "Enter a date of birth within the last 120 years.")

    def test_f5_a_date_of_birth_120_years_ago_is_allowed(self):
        oldest = date(timezone.localdate().year - 120, 1, 1)
        self.post_patient(date_of_birth=oldest.isoformat())
        self.assertTrue(Patient.objects.filter(name="Samira").exists())

    def test_f5_a_phone_with_letters_is_refused(self):
        response = self.post_patient(phone="03 ABC 001")
        self.assert_refused(response, "Enter a phone number with only digits, spaces, + and -.")

    def test_f5_a_phone_with_plus_dashes_and_spaces_is_allowed(self):
        self.post_patient(phone="+961 3-100-001")
        self.assertEqual(Patient.objects.get().phone, "+961 3-100-001")

    def test_f5_the_phone_can_still_be_left_empty(self):
        self.post_patient(phone="")
        self.assertEqual(Patient.objects.get().phone, "")

    def test_f5_a_patient_username_with_a_space_is_refused(self):
        response = self.post_patient(username="samira k", password="temporary1")
        self.assert_refused(response, "Enter a valid username.")

    def test_f5_a_patient_username_with_allowed_symbols_is_accepted(self):
        self.post_patient(username="samira.k@1", password="temporary1")
        self.assertEqual(Patient.objects.get().user.username, "samira.k@1")

    def test_f5_a_common_patient_password_is_refused(self):
        response = self.post_patient(username="samira", password="password")
        self.assert_refused(response, "This password is too common.")

    def test_f5_an_all_digits_patient_password_is_refused(self):
        response = self.post_patient(username="samira", password="12345678")
        self.assert_refused(response, "This password is entirely numeric.")

    def test_f5_a_patient_password_like_the_username_is_refused(self):
        response = self.post_patient(username="samirakhalil", password="samirakhalil1")
        self.assert_refused(response, "The password is too similar to the username.")


class AddLoginPageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("karim", "assistant"))

    def test_3_1_a_login_can_be_created_with_no_patient_record(self):
        response = self.client.post(
            "/patients/logins/new/",
            {"username": "omar", "password": "temporary1"},
            follow=True,
        )
        self.assertRedirects(response, "/patients/")
        self.assertContains(response, "Login created for omar.")
        omar = get_user_model().objects.get(username="omar")
        self.assertEqual(omar.role, "patient")
        self.assertFalse(Patient.objects.filter(user=omar).exists())

    def test_3_1_a_login_needs_a_username_and_a_password(self):
        response = self.client.post("/patients/logins/new/", {"username": "omar"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="omar").exists())

    def test_3_1_a_taken_username_is_refused(self):
        make_user("omar", "patient")
        response = self.client.post(
            "/patients/logins/new/", {"username": "omar", "password": "temporary1"}
        )
        self.assertContains(response, "That username is already taken.")

    def test_3_1_the_caregiver_temporary_password_is_hidden_as_it_is_typed(self):
        self.assertContains(self.client.get("/patients/logins/new/"), 'type="password"')

    def test_3_1_a_short_caregiver_password_is_refused(self):
        response = self.client.post(
            "/patients/logins/new/", {"username": "omar", "password": "short"}
        )
        self.assertContains(response, "at least 8 characters")
        self.assertFalse(get_user_model().objects.filter(username="omar").exists())

    def assert_login_refused(self, username, password, message):
        response = self.client.post("/patients/logins/new/", {"username": username, "password": password})
        self.assertContains(response, message)
        self.assertFalse(get_user_model().objects.filter(username=username).exists())

    def test_f5_a_caregiver_username_with_a_space_is_refused(self):
        self.assert_login_refused("omar k", "temporary1", "Enter a valid username.")

    def test_f5_a_common_caregiver_password_is_refused(self):
        self.assert_login_refused("omar", "password", "This password is too common.")

    def test_f5_an_all_digits_caregiver_password_is_refused(self):
        self.assert_login_refused("omar", "12345678", "This password is entirely numeric.")

    def test_f5_a_caregiver_password_like_the_username_is_refused(self):
        self.assert_login_refused("omarhaddad", "omarhaddad1", "The password is too similar to the username.")


class MyMedicationPassportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maya_user = make_user("maya", "patient")
        cls.maya = Patient.objects.create(
            name="Maya Farah", date_of_birth=date(1990, 9, 21), user=cls.maya_user
        )
        cls.father = Patient.objects.create(name="Maya's Father", date_of_birth=date(1950, 1, 5))
        cls.stranger = Patient.objects.create(name="Private Patient", date_of_birth=date(1980, 1, 1))
        cls.omar_user = make_user("omar", "patient")
        cls.staff = make_user("rana", "pharmacist")

        cls.penicillins = DrugClass.objects.create(name="Penicillins")
        cls.statins = DrugClass.objects.create(name="Statins")
        cls.maya_medicine = make_medicine("Atorvastatin", "scrum54-1", cls.statins)
        cls.father_medicine = make_medicine("Metformin", "scrum54-2", cls.statins)
        cls.private_medicine = make_medicine("PrivateMedicine", "scrum54-3", cls.statins)

        Allergy.objects.create(patient=cls.maya, drug_class=cls.penicillins)
        CurrentMedicine.objects.create(patient=cls.maya, medicine=cls.maya_medicine)
        CurrentMedicine.objects.create(patient=cls.father, medicine=cls.father_medicine)
        CurrentMedicine.objects.create(patient=cls.stranger, medicine=cls.private_medicine)

        maya_sale = Sale.objects.create(
            staff=cls.staff, payment_method="cash", source="counter", patient=cls.maya
        )
        SaleItem.objects.create(
            sale=maya_sale, medicine=cls.maya_medicine, quantity=3, unit_price=Decimal("5.00")
        )
        father_sale = Sale.objects.create(
            staff=cls.staff, payment_method="cash", source="counter", patient=cls.father
        )
        SaleItem.objects.create(
            sale=father_sale, medicine=cls.father_medicine, quantity=2, unit_price=Decimal("5.00")
        )
        CaregiverLink.objects.create(patient=cls.father, caregiver=cls.maya_user)
        CaregiverLink.objects.create(patient=cls.father, caregiver=cls.omar_user)

    def test_3_3_signed_out_visitors_are_redirected_to_patient_login(self):
        self.assertRedirects(self.client.get("/patients/my/"), "/login/?next=/patients/my/")

    def test_3_3_staff_cannot_open_a_patient_passport(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/patients/my/").status_code, 403)

    def test_3_3_maya_sees_her_allergies_medicines_and_dispensing_history(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Maya Farah")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "Atorvastatin")
        self.assertContains(response, "Dispensing History")
        self.assertContains(response, "rana")
        self.assertContains(response, "<td>3</td>", html=True)
        self.assertNotContains(response, "PrivateMedicine")

    def test_3_3_patient_navigation_links_to_passport(self):
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/home/"), 'href="/patients/my/"')

    def test_3_3_maya_can_switch_to_her_fathers_passport(self):
        self.client.force_login(self.maya_user)
        response = self.client.post(
            f"/family/view/{self.father.pk}/", {"next": "/patients/my/"}
        )
        self.assertRedirects(response, "/patients/my/")
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Maya&#x27;s Father", html=False)
        self.assertContains(response, "Metformin")
        self.assertContains(response, "<td>2</td>", html=True)
        self.assertNotContains(response, "Atorvastatin")

    def test_3_3_caregiver_without_own_patient_record_can_view_authorized_patient(self):
        self.client.force_login(self.omar_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Metformin")
        self.assertNotContains(response, "Atorvastatin")

    def test_3_3_maya_cannot_switch_to_unrelated_patient(self):
        self.client.force_login(self.maya_user)
        response = self.client.post(f"/family/view/{self.stranger.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get("/patients/my/"), "PrivateMedicine")

    def test_3_3_revoked_caregiver_cannot_view_old_passport(self):
        self.client.force_login(self.maya_user)
        self.client.post(f"/family/view/{self.father.pk}/")
        CaregiverLink.objects.filter(patient=self.father, caregiver=self.maya_user).update(active=False)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Atorvastatin")
        self.assertNotContains(response, "Metformin")

    def test_3_3_unlinked_user_sees_empty_state(self):
        self.client.force_login(make_user("unlinked", "patient"))
        response = self.client.get("/patients/my/")
        self.assertContains(response, "No patient passport is linked to your account")


class PassportSharingConsentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maya_user = make_user("maya_share", "patient")
        cls.maya = Patient.objects.create(
            name="Maya Share", date_of_birth=date(1990, 9, 21), phone="03 100 002", user=cls.maya_user
        )
        cls.stranger_user = make_user("stranger_share", "patient")
        cls.stranger = Patient.objects.create(
            name="Other Patient", date_of_birth=date(1985, 1, 1), user=cls.stranger_user
        )
        cls.caregiver = make_user("caregiver_share", "patient")
        CaregiverLink.objects.create(patient=cls.maya, caregiver=cls.caregiver)
        cls.staff = make_user("rana_share", "pharmacist")
        cls.penicillins = DrugClass.objects.create(name="Penicillins Share")
        cls.statins = DrugClass.objects.create(name="Statins Share")
        cls.medicine = make_medicine("Atorvastatin Share", "scrum55-1", cls.statins)
        Allergy.objects.create(patient=cls.maya, drug_class=cls.penicillins)
        CurrentMedicine.objects.create(patient=cls.maya, medicine=cls.medicine)
        sale = Sale.objects.create(
            staff=cls.staff, patient=cls.maya, payment_method="cash", source="counter"
        )
        SaleItem.objects.create(
            sale=sale, medicine=cls.medicine, quantity=3, unit_price=Decimal("5.00")
        )

    def share(self, **overrides):
        values = {"include_allergies": "on", "consent": "on", **overrides}
        return self.client.post("/patients/my/share/", values)

    def test_3_4a_share_page_requires_patient_login(self):
        self.assertRedirects(
            self.client.get("/patients/my/share/"),
            "/login/?next=/patients/my/share/",
        )

    def test_3_4a_staff_cannot_consent_as_a_patient(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/patients/my/share/").status_code, 403)

    def test_3_4a_patients_can_choose_sections_and_view_the_recipient(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/share/")
        self.assertContains(response, "Recipient: CarePharm")
        self.assertContains(response, "Allergies")
        self.assertContains(response, "Active medicines")
        self.assertContains(response, "Dispensing history")
        self.assertContains(response, "Confirm Consent")
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_consent_checkbox_is_required(self):
        self.client.force_login(self.maya_user)
        response = self.client.post("/patients/my/share/", {"include_allergies": "on"})
        self.assertContains(response, "Confirm your consent before sharing your passport.")
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_at_least_one_section_must_be_selected(self):
        self.client.force_login(self.maya_user)
        response = self.client.post("/patients/my/share/", {"consent": "on"})
        self.assertContains(response, "Select at least one section to share.")
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_staff_cannot_see_passport_sections_without_consent(self):
        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "has not shared their Medication Passport")
        self.assertNotContains(response, "Penicillins Share")
        self.assertNotContains(response, "Atorvastatin Share")

    def test_3_4a_patient_can_share_only_allergies(self):
        self.client.force_login(self.maya_user)
        self.assertRedirects(self.share(), "/patients/my/")
        share = PassportShare.objects.get(patient=self.maya)
        self.assertEqual(share.consented_by, self.maya_user)
        self.assertEqual(share.pharmacy_name, "CarePharm")
        self.assertTrue(share.include_allergies)
        self.assertFalse(share.include_medicines)
        self.assertFalse(share.include_history)
        self.assertIsNotNone(share.consented_at)
        self.assertEqual(Notification.objects.filter(user=self.staff).count(), 1)

        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "Penicillins Share")
        self.assertNotContains(response, "Atorvastatin Share")
        self.assertContains(response, "Not shared by patient", count=3)

    def test_3_4a_patient_can_share_medicines_and_history(self):
        self.client.force_login(self.maya_user)
        self.share(include_allergies="", include_medicines="on", include_history="on")
        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "Atorvastatin Share")
        self.assertContains(response, "<td>3</td>", html=True)
        self.assertNotContains(response, "Penicillins Share")

    def test_3_4a_updating_sections_revokes_the_old_consent(self):
        self.client.force_login(self.maya_user)
        self.share(include_allergies="on", include_medicines="on")
        previous = active_passport_share(self.maya)
        self.share(include_allergies="", include_history="on")
        previous.refresh_from_db()
        self.assertIsNotNone(previous.revoked_at)
        new_share = active_passport_share(self.maya)
        self.assertNotEqual(new_share.pk, previous.pk)
        self.assertTrue(new_share.include_history)
        self.assertFalse(new_share.include_allergies)
        self.assertFalse(new_share.include_medicines)

    def test_3_4a_caregiver_cannot_consent_on_behalf_of_patient(self):
        self.client.force_login(self.caregiver)
        self.assertEqual(self.client.get("/patients/my/share/").status_code, 403)
        self.assertEqual(self.share().status_code, 403)
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_sharing_applies_only_to_owners_record(self):
        self.client.force_login(self.stranger_user)
        self.share(patient=self.maya.pk)
        self.assertEqual(PassportShare.objects.get().patient, self.stranger)

    def test_3_4a_expired_or_revoked_grant_does_not_allow_staff_access(self):
        self.client.force_login(self.maya_user)
        self.share()
        share = PassportShare.objects.get()
        share.expires_at = timezone.now() - timedelta(days=1)
        share.save()
        self.assertIsNone(active_passport_share(self.maya))
        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertNotContains(response, "Penicillins Share")

    def test_3_4a_patient_passport_shows_share_button_and_state(self):
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), 'href="/patients/my/share/"')
        self.share()
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Send to a Pharmacy")
        self.assertContains(response, "Shared With")
        self.assertContains(response, "Active")

    def test_3_4a_patient_dashboard_matches_passport_sections_and_sharing_dialog(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "My Medication Passport")
        self.assertContains(response, "Download PDF")
        self.assertContains(response, "Send Your Passport")
        self.assertContains(response, "What to include")
        self.assertContains(response, "Contact details")
        self.assertContains(response, "Access lasts")
        self.assertContains(response, 'id="med-passport-share-dialog"')

    def test_3_4a_contact_is_private_until_selected(self):
        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertNotContains(response, "03 100 002")
        self.assertNotContains(response, "21 Sep 1990")

        self.client.force_login(self.maya_user)
        self.share(include_allergies="", include_contact="on")
        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "03 100 002")
        self.assertContains(response, "21 Sep 1990")
        self.assertNotContains(response, "Penicillins Share")

    def test_3_4a_share_duration_is_selected_and_access_expires(self):
        self.client.force_login(self.maya_user)
        self.share(access_days="3")
        share = PassportShare.objects.get(patient=self.maya)
        self.assertAlmostEqual(
            (share.expires_at - timezone.now()).total_seconds(),
            timedelta(days=3).total_seconds(),
            delta=10,
        )
        self.assertIsNotNone(active_passport_share(self.maya))
        PassportShare.objects.filter(pk=share.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(active_passport_share(self.maya))

    def test_3_4a_invalid_duration_is_rejected(self):
        self.client.force_login(self.maya_user)
        response = self.share(access_days="999")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4b_access_ends_at_exact_expiration_instant(self):
        self.client.force_login(self.maya_user)
        self.share(access_days="1")
        share = PassportShare.objects.get(patient=self.maya)

        with patch("passport.services.timezone.now", return_value=share.expires_at - timedelta(microseconds=1)):
            self.assertEqual(active_passport_share(self.maya), share)
        with patch("passport.services.timezone.now", return_value=share.expires_at):
            self.assertIsNone(active_passport_share(self.maya))

    def test_3_4b_expiry_automatically_hides_all_four_private_sections(self):
        self.client.force_login(self.maya_user)
        self.share(include_contact="on", include_medicines="on", include_history="on", access_days="1")
        share = PassportShare.objects.get(patient=self.maya)

        self.client.force_login(self.staff)
        before = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(before, "03 100 002")
        self.assertContains(before, "Penicillins Share")
        self.assertContains(before, "Atorvastatin Share")
        self.assertContains(before, "<td>3</td>", html=True)

        PassportShare.objects.filter(pk=share.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        after = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(after, "Pharmacy access has expired")
        self.assertNotContains(after, "03 100 002")
        self.assertNotContains(after, "Penicillins Share")
        self.assertNotContains(after, "Atorvastatin Share")
        self.assertNotContains(after, "<td>3</td>", html=True)
        self.assertContains(after, "Not shared by patient", count=4)

    def test_3_4b_patient_can_see_expired_status_and_share_again(self):
        self.client.force_login(self.maya_user)
        self.share(access_days="1")
        previous = PassportShare.objects.get(patient=self.maya)
        PassportShare.objects.filter(pk=previous.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        patient_page = self.client.get("/patients/my/")
        self.assertContains(patient_page, "Expired")
        self.assertContains(patient_page, "Share again to restore access")

        self.share(include_allergies="", include_medicines="on", access_days="7")
        current = active_passport_share(self.maya)
        self.assertIsNotNone(current)
        self.assertNotEqual(current.pk, previous.pk)
        self.client.force_login(self.staff)
        staff_page = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(staff_page, "Atorvastatin Share")
        self.assertNotContains(staff_page, "Penicillins Share")

    def test_3_4b_undated_legacy_consent_does_not_grant_indefinite_access(self):
        self.client.force_login(self.maya_user)
        self.share()
        PassportShare.objects.filter(patient=self.maya).update(expires_at=None)
        self.assertIsNone(active_passport_share(self.maya))

        self.client.force_login(self.staff)
        response = self.client.get(f"/patients/{self.maya.pk}/")
        self.assertContains(response, "Pharmacy access has expired")
        self.assertNotContains(response, "Penicillins Share")

        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), "Expired")
