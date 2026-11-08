from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from passport.models import Patient


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
        with self.assertRaises(IntegrityError), transaction.atomic():
            Patient.objects.create(name="Another", date_of_birth=date(1990, 1, 1), user=user)

    def test_t3_deleting_a_user_keeps_the_patient(self):
        user = get_user_model().objects.create_user(username="maya", password="pass")
        maya = Patient.objects.create(name="Maya", date_of_birth=date(1995, 9, 21), user=user)
        user.delete()
        maya.refresh_from_db()
        self.assertIsNone(maya.user)
