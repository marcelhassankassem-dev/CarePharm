from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from inventory.models import DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient
from prescriptions.models import PrescriptionItem
from prescriptions.services import create_prescription
from safety.services import safety_warnings


def make_medicine(name, strength, barcode, drug_class):
    return Medicine.objects.create(
        name=name,
        strength=strength,
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal("8.00"),
        minimum_quantity=10,
    )


class SafetyTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        penicillins = DrugClass.objects.create(name="Penicillins")
        statins = DrugClass.objects.create(name="Statins")
        antihistamines = DrugClass.objects.create(name="Antihistamines")
        cls.ampicillin = make_medicine("Ampicillin", "250 mg", "1000000000016", penicillins)
        cls.atorvastatin = make_medicine("Atorvastatin", "20 mg", "1000000000003", statins)
        cls.cetirizine = make_medicine("Cetirizine", "10 mg", "1000000000011", antihistamines)
        cls.ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))
        Allergy.objects.create(patient=cls.ahmad, drug_class=penicillins)
        CurrentMedicine.objects.create(patient=cls.ahmad, medicine=cls.atorvastatin)
        cls.samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.rana = get_user_model().objects.create(username="rana", role="pharmacist")


class AllergyWarningTests(SafetyTestData):
    def test_2_1a_a_medicine_in_an_allergy_class_gets_a_warning(self):
        warnings = safety_warnings(self.ahmad, self.ampicillin)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["title"], "Possible allergy conflict")
        self.assertEqual(warnings[0]["level"], "High")

    def test_2_1a_the_warning_names_the_allergy_and_the_medicine(self):
        warning = safety_warnings(self.ahmad, self.ampicillin)[0]
        self.assertEqual(
            warning["text"],
            "Patient has a recorded Penicillins allergy. Ampicillin 250 mg may not be safe.",
        )

    def test_2_1a_a_medicine_in_another_class_gets_no_warning(self):
        self.assertEqual(safety_warnings(self.ahmad, self.cetirizine), [])

    def test_2_1a_a_patient_with_no_allergies_gets_no_warning(self):
        self.assertEqual(safety_warnings(self.samira, self.ampicillin), [])


class SafetyPageTestData(SafetyTestData):
    def setUp(self):
        self.client.force_login(self.rana)
        self.prescription = create_prescription(self.ahmad)
        self.address = f"/prescriptions/{self.prescription.pk}/"

    def add(self, medicine, follow=False):
        entry = {"medicine": medicine.pk, "quantity": 20, "days_of_supply": 5}
        return self.client.post(f"{self.address}items/", entry, follow=follow)


class AllergyCheckPageTests(SafetyPageTestData):
    def test_2_1a_an_allergic_medicine_is_not_added(self):
        self.add(self.ampicillin)
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_2_1a_the_medication_safety_assistant_opens_instead(self):
        response = self.add(self.ampicillin)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Medication Safety Assistant")
        self.assertContains(response, "Review safety alerts before confirming a medicine for the patient.")

    def test_2_1a_the_page_shows_the_allergy_alert(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, "Possible allergy conflict")
        self.assertContains(response, "High")
        self.assertContains(
            response,
            "Patient has a recorded Penicillins allergy. Ampicillin 250 mg may not be safe.",
        )

    def test_2_1a_the_page_counts_the_alerts(self):
        self.assertContains(self.add(self.ampicillin), "1 alert found")

    def test_2_1a_the_page_shows_the_patients_allergies_and_medicines(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, "Ahmad")
        self.assertContains(response, "Known Allergies")
        self.assertContains(response, "<li><strong>Penicillins</strong></li>", html=True)
        self.assertContains(response, "Current Medications")
        self.assertContains(response, "<li><strong>Atorvastatin 20 mg</strong></li>", html=True)

    def test_2_1a_the_entry_keeps_what_was_typed(self):
        response = self.add(self.ampicillin)
        self.assertContains(
            response,
            f'<option value="{self.ampicillin.pk}" selected>Ampicillin 250 mg</option>',
            html=True,
        )
        self.assertContains(response, 'value="20"')
        self.assertContains(response, 'value="5"')

    def test_2_1a_run_safety_check_sends_the_entry_again(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, f'action="{self.address}items/"')
        self.assertContains(response, "Run Safety Check")

    def test_2_1a_cancel_goes_back_to_the_prescription(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, "Cancel")
        self.assertContains(response, f'href="{self.address}"')

    def test_2_1a_the_page_says_the_pharmacist_decides(self):
        self.assertContains(
            self.add(self.ampicillin),
            "Final decision is made by the pharmacist. The assistant provides guidance only.",
        )

    def test_2_1a_a_safe_medicine_is_added_as_before(self):
        response = self.add(self.cetirizine)
        self.assertRedirects(response, self.address)
        self.assertEqual(PrescriptionItem.objects.get().medicine, self.cetirizine)

    def test_2_1a_a_safe_medicine_says_no_alerts_were_found(self):
        response = self.add(self.cetirizine, follow=True)
        self.assertContains(response, "No safety alerts. Cetirizine 10 mg added.")


class DuplicateWarningTests(SafetyTestData):
    def test_2_1b_a_medicine_the_patient_already_takes_gets_a_warning(self):
        warnings = safety_warnings(self.ahmad, self.atorvastatin)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["title"], "Duplicate medication check")
        self.assertEqual(warnings[0]["level"], "Medium")

    def test_2_1b_the_warning_names_the_medicine(self):
        warning = safety_warnings(self.ahmad, self.atorvastatin)[0]
        self.assertEqual(warning["text"], "Patient already takes Atorvastatin 20 mg. Review before dispensing.")

    def test_2_1b_another_strength_is_not_a_duplicate(self):
        stronger = make_medicine("Atorvastatin", "40 mg", "1000000000099", self.atorvastatin.drug_class)
        self.assertEqual(safety_warnings(self.ahmad, stronger), [])

    def test_2_1b_a_patient_taking_nothing_gets_no_warning(self):
        self.assertEqual(safety_warnings(self.samira, self.atorvastatin), [])

    def test_2_1b_an_allergy_and_a_duplicate_give_two_warnings_allergy_first(self):
        CurrentMedicine.objects.create(patient=self.ahmad, medicine=self.ampicillin)
        warnings = safety_warnings(self.ahmad, self.ampicillin)
        self.assertEqual([warning["level"] for warning in warnings], ["High", "Medium"])


class DuplicateCheckPageTests(SafetyPageTestData):
    def test_2_1b_a_medicine_already_taken_is_not_added(self):
        self.add(self.atorvastatin)
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_2_1b_the_page_shows_the_duplicate_alert(self):
        response = self.add(self.atorvastatin)
        self.assertContains(response, "Duplicate medication check")
        self.assertContains(response, "Medium")
        self.assertContains(response, "Patient already takes Atorvastatin 20 mg. Review before dispensing.")
        self.assertContains(response, 'class="safety-alert medium"')

    def test_2_1b_the_duplicate_alert_has_its_own_icon(self):
        self.assertContains(self.add(self.atorvastatin), "safety-alert-medium.svg")

    def test_2_1b_both_alerts_are_counted(self):
        CurrentMedicine.objects.create(patient=self.ahmad, medicine=self.ampicillin)
        response = self.add(self.ampicillin)
        self.assertContains(response, "2 alerts found")
        self.assertContains(response, "Possible allergy conflict")
        self.assertContains(response, "Duplicate medication check")
