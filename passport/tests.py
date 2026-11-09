from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from inventory.models import DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient
from passport.services import create_patient, find_patients

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

    def test_3_1_search_with_no_match_finds_nobody(self):
        self.assertEqual(list(find_patients("nobody")), [])

    def test_3_1_each_listed_patient_knows_how_many_allergies_they_have(self):
        penicillins = DrugClass.objects.create(name="Penicillins")
        Allergy.objects.create(patient=self.samira, drug_class=penicillins)
        maya, samira = find_patients()
        self.assertEqual(maya.allergy_count, 0)
        self.assertEqual(samira.allergy_count, 1)


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
