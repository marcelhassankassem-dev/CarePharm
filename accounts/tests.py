from django.contrib.auth import get_user_model
from django.test import TestCase


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
