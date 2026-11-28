from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import CaregiverLink
from core.models import Notification
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from prescriptions.models import Prescription, PrescriptionItem
from prescriptions.services import mark_ready, start_preparing
from reservations.models import CaregiverRefillReminder, RefillRequest
from reservations.services import (
    approve_refill,
    delay_refill,
    request_refill,
    send_caregiver_refill_reminders,
    send_uncollected_refill_alerts,
)


class CaregiverRefillSprintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.omar = User.objects.create_user(username="omar", password="test-pass", role="patient")
        cls.father_user = User.objects.create_user(username="father", password="test-pass", role="patient")
        cls.other = User.objects.create_user(username="other", password="test-pass", role="patient")
        cls.pharmacist = User.objects.create_user(username="rana", password="test-pass", role="pharmacist")
        cls.father = Patient.objects.create(name="Khalil Nassar", date_of_birth=date(1949, 11, 5), user=cls.father_user)
        cls.link = CaregiverLink.objects.create(patient=cls.father, caregiver=cls.omar, active=True)
        drug_class = DrugClass.objects.create(name="Antidiabetics")
        cls.medicine = Medicine.objects.create(
            name="Metformin", strength="500 mg", barcode="7000000000001",
            drug_class=drug_class, price=Decimal("10.00"), minimum_quantity=5,
        )
        cls.prescription = Prescription.objects.create(
            patient=cls.father,
            status="collected",
            collected_at=timezone.now() - timedelta(days=27),
        )
        cls.item = PrescriptionItem.objects.create(
            prescription=cls.prescription, medicine=cls.medicine,
            quantity=30, days_of_supply=30, long_term=True,
        )

    def prepared_refill(self, ready_hours_ago=48):
        refill = request_refill(self.father_user, self.item)
        prescription = approve_refill(refill)
        Prescription.objects.filter(pk=prescription.pk).update(
            status="ready", ready_at=timezone.now() - timedelta(hours=ready_hours_ago)
        )
        return refill

    def test_59_caregiver_dashboard_has_real_medicines_and_refill_action(self):
        self.client.force_login(self.omar)
        response = self.client.get("/family/")
        self.assertContains(response, "Khalil Nassar’s Medicines")
        self.assertContains(response, "Metformin 500 mg")
        self.assertContains(response, f'action="/reservations/refills/request/{self.item.pk}/"')
        self.assertContains(response, "Refill reminder")
        self.assertContains(response, "Notifications")

    def test_59_caregiver_figma_cards_show_prescription_details_and_safe_family_action(self):
        self.client.force_login(self.omar)
        response = self.client.get("/family/")
        self.assertContains(response, f"Prescription #{self.prescription.pk}")
        self.assertContains(response, "30 units · 30-day supply")
        self.assertContains(response, f"/prescriptions/mine/#prescription-{self.prescription.pk}")
        self.assertContains(response, "View Details")
        self.assertContains(response, "More options for Metformin 500 mg")
        self.assertContains(response, 'data-family-add-open')
        self.assertContains(response, "your family member must sign in to their own CarePharm account")

    def test_59_prescription_details_link_back_and_explain_new_refill_status(self):
        request_refill(self.omar, self.item)
        self.client.force_login(self.omar)
        response = self.client.get("/prescriptions/mine/")
        self.assertContains(response, 'href="/family/">← Back to Family Access</a>')
        self.assertContains(response, "Current refill requests")
        self.assertContains(response, "Refill requested — awaiting pharmacy review")
        self.assertContains(response, f'id="prescription-{self.prescription.pk}"')
        self.assertContains(response, "Collected")

        self.client.force_login(self.other)
        self.assertNotContains(self.client.get("/prescriptions/mine/"), "Back to Family Access")

    def test_59_caregiver_requests_from_family_dashboard_and_is_notified(self):
        self.client.force_login(self.omar)
        response = self.client.post(
            f"/reservations/refills/request/{self.item.pk}/", {"return_to": "family"}
        )
        self.assertRedirects(response, "/family/")
        refill = RefillRequest.objects.get()
        self.assertEqual((refill.requested_by, refill.prescription_item), (self.omar, self.item))
        self.assertTrue(Notification.objects.filter(user=self.omar, text__startswith="Refill requested").exists())
        self.assertContains(self.client.get("/family/"), "Refill requested")

    def test_59_dashboard_hides_refill_button_while_an_approved_refill_is_uncollected(self):
        self.prepared_refill()
        self.client.force_login(self.omar)
        response = self.client.get("/family/")
        self.assertNotContains(response, f'action="/reservations/refills/request/{self.item.pk}/"')
        self.assertContains(response, "Refill ready for pickup")
        self.assertEqual(RefillRequest.objects.count(), 1)

    def test_59_caregiver_gets_approval_delay_and_ready_notifications(self):
        first = request_refill(self.father_user, self.item)
        delay_refill(first, timezone.localdate() + timedelta(days=7))
        self.assertTrue(Notification.objects.filter(user=self.omar, text__startswith="Refill delayed").exists())
        prescription = approve_refill(first)
        self.assertTrue(Notification.objects.filter(user=self.omar, text__startswith="Refill approved").exists())
        Batch.objects.create(
            medicine=self.medicine, batch_number="M500", quantity=90,
            expiry_date=timezone.localdate() + timedelta(days=365),
            unit_cost=Decimal("5.00"), supplier="Test wholesaler",
        )
        start_preparing(prescription)
        mark_ready(self.pharmacist, prescription)
        self.assertTrue(Notification.objects.filter(
            user=self.omar, text__contains="ready for pickup", link="/family/"
        ).exists())
        self.assertTrue(Notification.objects.filter(user=self.father_user, text__contains="ready for pickup").exists())

    def test_59_reminder_sent_once_per_day_and_only_to_authorized_caregivers(self):
        self.assertEqual(send_caregiver_refill_reminders(), 1)
        self.assertEqual(send_caregiver_refill_reminders(), 0)
        self.assertEqual(CaregiverRefillReminder.objects.filter(caregiver=self.omar).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.omar, text__startswith="Refill reminder").count(), 1)
        self.assertFalse(Notification.objects.filter(user=self.other).exists())

    def test_59_revoked_caregiver_sees_no_medicines_or_new_alerts(self):
        self.link.active = False
        self.link.save(update_fields=["active"])
        self.client.force_login(self.omar)
        self.assertNotContains(self.client.get("/family/"), "Khalil Nassar’s Medicines")
        self.assertEqual(self.client.post(f"/reservations/refills/request/{self.item.pk}/").status_code, 404)
        self.assertEqual(send_caregiver_refill_reminders(), 0)
        self.assertFalse(Notification.objects.filter(user=self.omar).exists())

    def test_60_before_48_hours_there_is_no_uncollected_refill_alert(self):
        self.prepared_refill(47)
        self.assertEqual(send_uncollected_refill_alerts(), 0)
        self.assertFalse(Notification.objects.filter(text__startswith="Refill not collected").exists())

    def test_60_after_48_hours_alerts_once_and_is_saved(self):
        refill = self.prepared_refill(49)
        self.assertEqual(send_uncollected_refill_alerts(), 1)
        self.assertEqual(send_uncollected_refill_alerts(), 0)
        refill.refresh_from_db()
        self.assertIsNotNone(refill.pickup_alert_sent_at)
        self.assertEqual(Notification.objects.filter(user=self.omar, text__startswith="Refill not collected").count(), 1)
        self.assertFalse(Notification.objects.filter(user=self.other).exists())

    def test_60_collected_or_returned_refills_do_not_trigger_alerts(self):
        for status in ("collected", "returned"):
            with self.subTest(status=status):
                refill = self.prepared_refill(50)
                Prescription.objects.filter(pk=refill.new_prescription_id).update(status=status)
                self.assertEqual(send_uncollected_refill_alerts(), 0)
                RefillRequest.objects.filter(pk=refill.pk).delete()

    def test_60_newly_revoked_caregiver_is_never_alerted(self):
        refill = self.prepared_refill(50)
        self.link.active = False
        self.link.save(update_fields=["active"])
        self.assertEqual(send_uncollected_refill_alerts(), 0)
        refill.refresh_from_db()
        self.assertIsNone(refill.pickup_alert_sent_at)
        self.assertFalse(Notification.objects.filter(user=self.omar, text__startswith="Refill not collected").exists())

    def test_60_alerts_all_current_caregivers_but_not_unlinked_users(self):
        CaregiverLink.objects.create(patient=self.father, caregiver=self.other, active=True)
        self.prepared_refill(51)
        self.assertEqual(send_uncollected_refill_alerts(), 1)
        self.assertEqual(Notification.objects.filter(text__startswith="Refill not collected").count(), 2)

    def test_60_family_page_triggers_due_alert_for_logged_in_caregiver(self):
        self.prepared_refill(51)
        self.client.force_login(self.omar)
        self.assertContains(self.client.get("/family/"), "Refill not collected")
        self.assertEqual(Notification.objects.filter(user=self.omar, text__startswith="Refill not collected").count(), 1)

    def test_60_management_command_can_run_repeatedly(self):
        self.prepared_refill(50)
        output = StringIO()
        call_command("process_caregiver_alerts", stdout=output)
        self.assertIn("1 uncollected-refill alerts", output.getvalue())
        output = StringIO()
        call_command("process_caregiver_alerts", stdout=output)
        self.assertIn("0 uncollected-refill alerts", output.getvalue())


class CaregiverDemoSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())

    def test_59_omar_has_a_refill_button_for_a_seeded_family_medicine(self):
        self.client.force_login(get_user_model().objects.get(username="omar"))
        response = self.client.get("/family/")
        self.assertTrue(response.context["caregiver_view"])
        self.assertEqual(response.context["viewing"].name, "Omar's Father")
        self.assertContains(response, "Amlodipine 5 mg")
        self.assertContains(response, "Request Refill")
        self.assertContains(response, "Refill delayed")
