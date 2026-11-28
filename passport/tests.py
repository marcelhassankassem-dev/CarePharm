from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from accounts.models import CaregiverLink
from inventory.models import DrugClass, Medicine
from passport.forms import PatientChoiceForm
from passport.models import Allergy, CurrentMedicine, PassportShare, Patient
from core.services import notify
from passport.services import (
    add_current_medicine,
    age_of,
    create_patient,
    find_patients,
    get_share,
    open_shares_of,
    passport_medicines,
    patient_header,
    patient_name_for,
    patient_of,
    patients_cared_for,
    remove_share,
    share_is_open,
    share_passport,
    share_rows,
    viewable_patients,
)
from prescriptions.models import Prescription, PrescriptionItem
from reservations.services import next_refill_dates
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

    def test_3_1_the_passport_shows_the_age_and_passport_id(self):
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(response, f"{age_of(self.samira)} years")
        self.assertContains(response, f"CP-{self.samira.pk:05d}")

    def test_3_1_staff_always_see_the_whole_passport_of_a_patient_with_a_login(self):
        samira_user = make_user("samira", "patient")
        Patient.objects.filter(pk=self.samira.pk).update(user=samira_user)
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(response, "03 100 001")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "Atorvastatin 500 mg")
        self.assertNotContains(response, "Not shared by patient")

    def test_3_1_the_list_flags_every_patient_with_an_allergy(self):
        samira_user = make_user("samira", "patient")
        Patient.objects.filter(pk=self.samira.pk).update(user=samira_user)
        self.assertContains(self.client.get(f"/patients/{self.maya.pk}/"), ">Allergy<", count=1)

    def test_3_4a_the_sharing_card_says_when_nothing_is_shared(self):
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(response, "Passport Sharing")
        self.assertContains(response, "Not shared with another pharmacy")

    def test_3_4a_the_sharing_card_lists_the_pharmacies_with_open_access(self):
        share_passport(self.samira, "Byblos Family Pharmacy", ["allergies"], 7)
        ended = share_passport(self.samira, "Saida Health Pharmacy", ["allergies"], 7)
        remove_share(ended)
        response = self.client.get(f"/patients/{self.samira.pk}/")
        self.assertContains(response, "Byblos Family Pharmacy")
        self.assertNotContains(response, "Saida Health Pharmacy")
        self.assertNotContains(response, "Not shared with another pharmacy")

    def test_1_1_new_prescription_starts_with_the_selected_patient(self):
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
        self.assertContains(response, "<td>3</td>", html=True)
        self.assertNotContains(response, "PrivateMedicine")

    def test_3_3_page_uses_the_figma_wording(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "MEDICATION PASSPORT")
        self.assertContains(response, "My Medication Passport")
        self.assertContains(response, "in one record you can share with any pharmacy.")
        self.assertContains(response, "Checked by the pharmacist before every medicine")
        self.assertContains(response, "What you are taking now")
        self.assertContains(response, "Every medicine you collected")
        self.assertContains(response, "Download PDF")
        self.assertContains(response, "Send to a Pharmacy")

    def test_3_3_the_passport_card_shows_the_patient_details(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "21 Sep 1990")
        self.assertContains(response, f"CP-{self.maya.pk:05d}")

    def test_3_3_history_names_the_pharmacy_not_the_staff_member(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(first_name="Rana", last_name="Haddad")
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "<th>PHARMACY</th>", html=True)
        self.assertNotContains(response, "Rana Haddad")

    def test_3_3_active_medicines_show_how_many_there_are(self):
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), "1 medicine<")

    def test_3_3_a_long_term_medicine_shows_its_next_refill_date(self):
        collected_at = timezone.now() - timedelta(days=10)
        prescription = Prescription.objects.create(patient=self.maya, status="collected", collected_at=collected_at)
        PrescriptionItem.objects.create(
            prescription=prescription, medicine=self.maya_medicine, quantity=30, days_of_supply=30, long_term=True
        )
        refill_on = timezone.localtime(collected_at).date() + timedelta(days=30)
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), f"{refill_on.day} {refill_on:%b %Y}")

    def test_3_3_a_medicine_with_no_long_term_prescription_needs_no_refill(self):
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), "No refill needed")

    def test_3_3_the_family_switcher_is_shown_once(self):
        self.client.force_login(self.maya_user)
        self.assertContains(self.client.get("/patients/my/"), 'class="profile-switch"', count=1)

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


class NextRefillDatesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maya = Patient.objects.create(name="Maya", date_of_birth=date(1990, 9, 21))
        statins = DrugClass.objects.create(name="Statins")
        cls.atorvastatin = make_medicine("Atorvastatin", "1", statins)
        cls.vitamin = make_medicine("Vitamin D3", "2", statins)

    def collect(self, medicine, days_ago, days_of_supply, long_term=True):
        collected_at = timezone.now() - timedelta(days=days_ago)
        prescription = Prescription.objects.create(patient=self.maya, status="collected", collected_at=collected_at)
        PrescriptionItem.objects.create(
            prescription=prescription,
            medicine=medicine,
            quantity=30,
            days_of_supply=days_of_supply,
            long_term=long_term,
        )
        return timezone.localtime(collected_at).date() + timedelta(days=days_of_supply)

    def test_3_3_the_next_refill_is_when_the_latest_supply_runs_out(self):
        self.collect(self.atorvastatin, 40, 30)
        runs_out_on = self.collect(self.atorvastatin, 10, 30)
        self.assertEqual(next_refill_dates(self.maya), {self.atorvastatin.pk: runs_out_on})

    def test_3_3_a_medicine_that_is_not_long_term_has_no_next_refill(self):
        self.collect(self.vitamin, 10, 30, long_term=False)
        self.assertEqual(next_refill_dates(self.maya), {})

    def test_3_3_passport_medicines_pair_each_current_medicine_with_its_refill(self):
        CurrentMedicine.objects.create(patient=self.maya, medicine=self.atorvastatin)
        CurrentMedicine.objects.create(patient=self.maya, medicine=self.vitamin)
        runs_out_on = self.collect(self.atorvastatin, 10, 30)
        rows = passport_medicines(self.maya, next_refill_dates(self.maya))
        self.assertEqual(
            rows,
            [
                {"medicine": self.atorvastatin, "next_refill": runs_out_on},
                {"medicine": self.vitamin, "next_refill": None},
            ],
        )


class AgeTests(TestCase):
    def test_3_1_age_counts_only_birthdays_already_reached(self):
        patient = Patient(name="Samira", date_of_birth=date(1958, 3, 14))
        self.assertEqual(age_of(patient, date(2026, 3, 13)), 67)
        self.assertEqual(age_of(patient, date(2026, 3, 14)), 68)


class SharePassportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maya = Patient.objects.create(name="Maya", date_of_birth=date(1990, 9, 21))
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))

    def test_3_4a_a_share_saves_the_pharmacy_and_the_chosen_sections(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["contact", "allergies"], 7)
        self.assertEqual(share.patient, self.maya)
        self.assertEqual(share.pharmacy_name, "Byblos Family Pharmacy")
        self.assertTrue(share.include_contact)
        self.assertTrue(share.include_allergies)
        self.assertFalse(share.include_medicines)
        self.assertFalse(share.include_history)
        self.assertFalse(share.revoked)

    def test_3_4a_the_pharmacy_name_is_optional(self):
        self.assertEqual(share_passport(self.maya, "", ["allergies"], 7).pharmacy_name, "")

    def test_3_4a_each_share_gets_its_own_hard_to_guess_link(self):
        first = share_passport(self.maya, "", ["allergies"], 7)
        second = share_passport(self.maya, "", ["allergies"], 7)
        self.assertNotEqual(first.token, second.token)
        self.assertGreaterEqual(len(first.token), 20)

    def test_3_4a_at_least_one_section_must_be_chosen(self):
        with self.assertRaises(ValidationError):
            share_passport(self.maya, "", [], 7)
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4b_access_lasts_the_chosen_number_of_days(self):
        share = share_passport(self.maya, "", ["allergies"], 3)
        self.assertAlmostEqual(
            (share.expires_at - timezone.now()).total_seconds(), timedelta(days=3).total_seconds(), delta=10
        )

    def test_3_4b_only_the_offered_numbers_of_days_are_allowed(self):
        with self.assertRaises(ValidationError):
            share_passport(self.maya, "", ["allergies"], 999)
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_sharing_again_keeps_the_earlier_pharmacies(self):
        share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        share_passport(self.maya, "Saida Health Pharmacy", ["history"], 7)
        self.assertEqual(len(open_shares_of(self.maya)), 2)

    def test_3_4b_a_share_is_open_until_the_exact_moment_it_expires(self):
        share = share_passport(self.maya, "", ["allergies"], 1)
        just_before = share.expires_at - timedelta(microseconds=1)
        with patch("passport.services.timezone.now", return_value=just_before):
            self.assertTrue(share_is_open(share))
        with patch("passport.services.timezone.now", return_value=share.expires_at):
            self.assertFalse(share_is_open(share))

    def test_3_5_removing_a_share_closes_it_at_once(self):
        share = share_passport(self.maya, "", ["allergies"], 30)
        remove_share(share)
        share.refresh_from_db()
        self.assertTrue(share.revoked)
        self.assertFalse(share_is_open(share))
        self.assertEqual(open_shares_of(self.maya), [])

    def test_3_5_a_patient_can_only_reach_their_own_shares(self):
        share = share_passport(self.maya, "", ["allergies"], 7)
        self.assertEqual(get_share(self.maya, share.pk), share)
        with self.assertRaises(Http404):
            get_share(self.samira, share.pk)

    def test_3_4b_each_share_is_listed_newest_first_with_its_status(self):
        removed = share_passport(self.maya, "Tyre Pharmacy", ["allergies"], 7)
        remove_share(removed)
        expired = share_passport(self.maya, "Saida Health Pharmacy", ["allergies"], 7)
        PassportShare.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(days=1))
        active = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        rows = share_rows(self.maya)
        self.assertEqual([row["share"] for row in rows], [active, expired, removed])
        self.assertEqual([row["status"] for row in rows], ["Active", "Expired", "Removed"])


class SendPassportPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maya_user = make_user("maya", "patient")
        cls.maya = Patient.objects.create(name="Maya", date_of_birth=date(1990, 9, 21), user=cls.maya_user)
        cls.father = Patient.objects.create(name="Maya's Father", date_of_birth=date(1950, 1, 5))
        cls.omar = make_user("omar", "patient")
        CaregiverLink.objects.create(patient=cls.father, caregiver=cls.omar)
        cls.stranger_user = make_user("stranger", "patient")
        Patient.objects.create(name="Stranger", date_of_birth=date(1980, 1, 1), user=cls.stranger_user)

    def send(self, **changes):
        values = {"pharmacy_name": "Byblos Family Pharmacy", "sections": ["allergies"], "days": "7", "consent": "on"}
        values.update(changes)
        return self.client.post("/patients/my/share/", values)

    def test_3_4a_signed_out_visitors_are_sent_to_the_patient_login(self):
        self.assertRedirects(self.client.get("/patients/my/share/"), "/login/?next=/patients/my/share/")

    def test_3_4a_staff_cannot_send_a_passport(self):
        self.client.force_login(make_user("rana", "pharmacist"))
        self.assertEqual(self.client.get("/patients/my/share/").status_code, 403)

    def test_3_4a_the_popup_uses_the_figma_wording(self):
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/share/")
        self.assertContains(response, "Send Your Passport")
        self.assertContains(response, "Choose a pharmacy and what it can see.")
        self.assertContains(response, "What to include")
        self.assertContains(response, "Contact details")
        self.assertContains(response, "Active medicines")
        self.assertContains(response, "Dispensing history")
        self.assertContains(response, "Access lasts")
        self.assertContains(response, "I agree to share this medical information with the pharmacy above.")
        self.assertContains(response, "Send Passport")
        self.assertContains(response, "My Medication Passport")

    def test_3_4a_sending_creates_a_link_and_shows_it(self):
        self.client.force_login(self.maya_user)
        self.assertRedirects(self.send(), "/patients/my/")
        share = PassportShare.objects.get()
        self.assertEqual(share.patient, self.maya)
        response = self.client.get("/patients/my/")
        self.assertContains(response, f"/passport/{share.token}/")
        self.assertContains(response, "Byblos Family Pharmacy")

    def test_3_4a_consent_must_be_given(self):
        self.client.force_login(self.maya_user)
        response = self.send(consent="")
        self.assertContains(response, "Confirm your consent before sending your passport.")
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_at_least_one_section_must_be_ticked(self):
        self.client.force_login(self.maya_user)
        response = self.send(sections=[])
        self.assertContains(response, "Choose at least one section to share.")
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4b_an_unknown_number_of_days_is_refused(self):
        self.client.force_login(self.maya_user)
        self.assertEqual(self.send(days="999").status_code, 200)
        self.assertFalse(PassportShare.objects.exists())

    def test_3_4a_a_caregiver_sends_the_passport_of_the_patient_they_are_viewing(self):
        self.client.force_login(self.omar)
        self.send()
        self.assertEqual(PassportShare.objects.get().patient, self.father)

    def test_3_4b_the_shared_with_list_shows_when_access_ends(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        ends = timezone.localtime(share.expires_at)
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Shared With")
        self.assertContains(response, f"access until {ends.day} {ends:%b}")
        self.assertContains(response, "Active")

    def test_3_4b_an_expired_share_is_listed_as_expired(self):
        share = share_passport(self.maya, "Saida Health Pharmacy", ["allergies"], 7)
        PassportShare.objects.filter(pk=share.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.client.force_login(self.maya_user)
        response = self.client.get("/patients/my/")
        self.assertContains(response, "Expired")
        self.assertNotContains(response, f"/passport/{share.token}/")

    def test_3_5_remove_access_asks_first(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        self.client.force_login(self.maya_user)
        response = self.client.get(f"/patients/my/share/{share.pk}/remove/")
        self.assertContains(response, "Remove Access")
        self.assertContains(response, "Byblos Family Pharmacy will no longer be able to open your passport.")
        share.refresh_from_db()
        self.assertFalse(share.revoked)

    def test_3_5_confirming_removes_access_at_once(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        self.client.force_login(self.maya_user)
        self.assertRedirects(self.client.post(f"/patients/my/share/{share.pk}/remove/"), "/patients/my/")
        share.refresh_from_db()
        self.assertTrue(share.revoked)
        self.assertContains(self.client.get("/patients/my/"), "Removed")

    def test_3_5_another_patients_share_cannot_be_removed(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        self.client.force_login(self.stranger_user)
        self.assertEqual(self.client.post(f"/patients/my/share/{share.pk}/remove/").status_code, 404)
        share.refresh_from_db()
        self.assertFalse(share.revoked)


class SharedPassportLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        staff = make_user("rana", "pharmacist")
        cls.maya = Patient.objects.create(name="Maya Farah", date_of_birth=date(1990, 9, 21), phone="03 100 002")
        penicillins = DrugClass.objects.create(name="Penicillins")
        statins = DrugClass.objects.create(name="Statins")
        atorvastatin = make_medicine("Atorvastatin", "1", statins)
        metformin = make_medicine("Metformin", "2", statins)
        Allergy.objects.create(patient=cls.maya, drug_class=penicillins)
        CurrentMedicine.objects.create(patient=cls.maya, medicine=atorvastatin)
        sale = Sale.objects.create(staff=staff, payment_method="cash", source="counter", patient=cls.maya)
        SaleItem.objects.create(sale=sale, medicine=metformin, quantity=3, unit_price=Decimal("5.00"))

    def open_link(self, sections):
        share = share_passport(self.maya, "Byblos Family Pharmacy", sections, 7)
        return share, self.client.get(f"/passport/{share.token}/")

    def test_3_4a_the_link_opens_without_signing_in(self):
        share, response = self.open_link(["allergies"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Maya Farah")

    def test_3_4a_the_link_shows_only_the_chosen_sections(self):
        share, response = self.open_link(["allergies", "medicines"])
        self.assertContains(response, "Penicillins")
        self.assertContains(response, "Atorvastatin")
        self.assertNotContains(response, "03 100 002")
        self.assertNotContains(response, "21 Sep 1990")
        self.assertNotContains(response, "Metformin")

    def test_3_4a_the_contact_section_shows_the_phone_and_date_of_birth(self):
        share, response = self.open_link(["contact"])
        self.assertContains(response, "03 100 002")
        self.assertContains(response, "21 Sep 1990")
        self.assertNotContains(response, "Penicillins")

    def test_3_4a_the_history_section_shows_what_was_dispensed(self):
        share, response = self.open_link(["history"])
        self.assertContains(response, "Metformin")
        self.assertContains(response, "<td>3</td>", html=True)
        self.assertNotContains(response, "Atorvastatin")

    def test_3_4a_the_link_says_when_access_ends(self):
        share, response = self.open_link(["allergies"])
        ends = timezone.localtime(share.expires_at)
        self.assertContains(response, f"Access ends {ends.day} {ends:%b %Y}")

    def test_3_4b_an_expired_link_shows_nothing(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["contact", "allergies"], 7)
        PassportShare.objects.filter(pk=share.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(f"/passport/{share.token}/")
        self.assertContains(response, "This passport link has ended.")
        self.assertNotContains(response, "Penicillins")
        self.assertNotContains(response, "03 100 002")

    def test_3_5_a_removed_link_shows_nothing(self):
        share = share_passport(self.maya, "Byblos Family Pharmacy", ["allergies"], 7)
        remove_share(share)
        response = self.client.get(f"/passport/{share.token}/")
        self.assertContains(response, "This passport link has ended.")
        self.assertNotContains(response, "Penicillins")

    def test_3_4a_an_unknown_link_is_not_found(self):
        self.assertEqual(self.client.get("/passport/not-a-real-link/").status_code, 404)
