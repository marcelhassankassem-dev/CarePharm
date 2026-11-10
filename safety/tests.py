from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ActivityLog
from inventory.models import DrugClass, Medicine
from passport.models import Allergy, CurrentMedicine, Patient
from prescriptions.models import PrescriptionItem
from prescriptions.services import create_prescription, delete_prescription
from safety.models import SafetyOverride
from safety.services import override_warnings, safety_warnings

REASON = "Prescriber confirmed the allergy is mild."


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


class OverrideServiceTests(SafetyTestData):
    def setUp(self):
        self.prescription = create_prescription(self.ahmad)

    def override(self, medicine, reason=REASON):
        return override_warnings(self.rana, self.prescription, medicine, 20, 5, False, reason)

    def test_2_2_each_warning_says_its_kind(self):
        CurrentMedicine.objects.create(patient=self.ahmad, medicine=self.ampicillin)
        warnings = safety_warnings(self.ahmad, self.ampicillin)
        self.assertEqual([warning["kind"] for warning in warnings], ["allergy", "duplicate"])

    def test_2_2_overriding_adds_the_medicine(self):
        self.override(self.ampicillin)
        item = PrescriptionItem.objects.get()
        self.assertEqual(item.prescription, self.prescription)
        self.assertEqual(item.medicine, self.ampicillin)
        self.assertEqual(item.quantity, 20)
        self.assertEqual(item.days_of_supply, 5)

    def test_2_2_the_override_saves_who_why_and_which_warning(self):
        self.override(self.ampicillin)
        saved = SafetyOverride.objects.get()
        self.assertEqual(saved.prescription, self.prescription)
        self.assertEqual(saved.medicine, self.ampicillin)
        self.assertEqual(saved.warning_type, "allergy")
        self.assertEqual(saved.reason, REASON)
        self.assertEqual(saved.pharmacist, self.rana)
        self.assertIsNotNone(saved.time)

    def test_2_2_each_warning_gets_its_own_override(self):
        CurrentMedicine.objects.create(patient=self.ahmad, medicine=self.ampicillin)
        self.override(self.ampicillin)
        types = [saved.warning_type for saved in SafetyOverride.objects.order_by("id")]
        self.assertEqual(types, ["allergy", "duplicate"])

    def test_2_2_a_reason_is_required(self):
        with self.assertRaisesMessage(ValidationError, "Enter a reason to continue."):
            self.override(self.ampicillin, reason="   ")
        self.assertFalse(PrescriptionItem.objects.exists())
        self.assertFalse(SafetyOverride.objects.exists())

    def test_2_2_the_decision_is_logged_for_the_pharmacist(self):
        self.override(self.ampicillin)
        entry = ActivityLog.objects.get(action="Safety decision")
        self.assertEqual(entry.user, self.rana)
        self.assertEqual(entry.details, f"Ampicillin 250 mg for Ahmad · Possible allergy conflict · Reason: {REASON}")

    def test_2_2_nothing_is_saved_if_the_decision_cannot_be_logged(self):
        with patch("safety.services.log_action", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self.override(self.ampicillin)
        self.assertFalse(PrescriptionItem.objects.exists())
        self.assertFalse(SafetyOverride.objects.exists())

    def test_2_2_deleting_a_received_prescription_deletes_its_overrides(self):
        self.override(self.ampicillin)
        delete_prescription(self.prescription)
        self.assertFalse(SafetyOverride.objects.exists())
        self.assertTrue(ActivityLog.objects.filter(action="Safety decision").exists())


class OverridePageTests(SafetyPageTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.hala = User.objects.create(username="hala", role="owner")
        cls.nadim = User.objects.create(username="nadim", role="owner", is_pharmacist=True)

    def override(self, reason=REASON, follow=False):
        entry = {"medicine": self.ampicillin.pk, "quantity": 20, "days_of_supply": 5, "override": "1", "reason": reason}
        return self.client.post(f"{self.address}items/", entry, follow=follow)

    def test_2_2_the_decision_card_offers_override_with_note(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, "Override with Note")
        self.assertContains(response, "Pharmacist note")
        self.assertContains(response, 'name="reason"')
        self.assertContains(response, "Choose how you would like to proceed. You can still override with a clinical note.")

    def test_2_2_the_override_form_carries_the_entry(self):
        response = self.add(self.ampicillin)
        self.assertContains(response, f'<input type="hidden" name="medicine" value="{self.ampicillin.pk}">', html=True)
        self.assertContains(response, '<input type="hidden" name="quantity" value="20">', html=True)
        self.assertContains(response, '<input type="hidden" name="days_of_supply" value="5">', html=True)

    def test_2_2_overriding_with_a_reason_adds_the_medicine(self):
        self.assertRedirects(self.override(), self.address)
        self.assertEqual(PrescriptionItem.objects.get().medicine, self.ampicillin)
        self.assertEqual(SafetyOverride.objects.get().reason, REASON)

    def test_2_2_overriding_says_what_was_added(self):
        self.assertContains(self.override(follow=True), "Warning overridden. Ampicillin 250 mg added.")

    def test_2_2_overriding_without_a_reason_keeps_the_safety_page(self):
        response = self.override(reason="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Medication Safety Assistant")
        self.assertContains(response, "Enter a reason to continue.")
        self.assertFalse(PrescriptionItem.objects.exists())

    def test_2_2_an_assistant_cannot_override(self):
        self.client.force_login(self.karim)
        self.assertEqual(self.override().status_code, 403)
        self.assertFalse(SafetyOverride.objects.exists())

    def test_2_2_an_owner_who_is_not_a_pharmacist_cannot_override(self):
        self.client.force_login(self.hala)
        self.assertEqual(self.override().status_code, 403)
        self.assertFalse(SafetyOverride.objects.exists())

    def test_2_2_an_owner_who_is_a_pharmacist_can_override(self):
        self.client.force_login(self.nadim)
        self.assertRedirects(self.override(), self.address)
        self.assertEqual(SafetyOverride.objects.get().pharmacist, self.nadim)
