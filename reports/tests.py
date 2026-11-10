from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from prescriptions.models import Prescription
from reports.services import prescription_status_counts, prescriptions_to_prepare, stock_alerts


def make_medicine(name, barcode, minimum_quantity):
    drug_class, created = DrugClass.objects.get_or_create(name="Test class")
    return Medicine.objects.create(
        name=name,
        strength="500 mg",
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal("5.00"),
        minimum_quantity=minimum_quantity,
    )


def make_batch(medicine, batch_number, quantity, days_until_expiry):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=timezone.localdate() + timedelta(days=days_until_expiry),
        quantity=quantity,
        unit_cost=Decimal("2.00"),
        supplier="Cedar Medical Supplies",
    )


class DashboardTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.nadim = User.objects.create(username="nadim", role="owner", is_pharmacist=True)
        cls.rana = User.objects.create(username="rana", role="pharmacist")
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.maya = User.objects.create(username="maya", role="patient")

        samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        ahmad = Patient.objects.create(name="Ahmad", date_of_birth=date(1980, 6, 2))
        cls.received = Prescription.objects.create(patient=samira, status="received")
        cls.preparing = Prescription.objects.create(patient=ahmad, status="preparing")
        cls.ready = Prescription.objects.create(patient=samira, status="ready")
        cls.ready_too = Prescription.objects.create(patient=ahmad, status="ready")
        cls.collected = Prescription.objects.create(patient=samira, status="collected")
        cls.returned = Prescription.objects.create(patient=ahmad, status="returned")

        cls.amoxicillin = make_medicine("Amoxicillin", "1000000000001", 20)
        make_batch(cls.amoxicillin, "AMX-001", 8, 300)
        cls.ibuprofen = make_medicine("Ibuprofen", "1000000000002", 10)
        make_batch(cls.ibuprofen, "IBU-001", 100, 30)
        cls.cetirizine = make_medicine("Cetirizine", "1000000000003", 10)
        make_batch(cls.cetirizine, "CET-001", 100, 300)


class DashboardServiceTests(DashboardTestData):
    def statuses(self, rows):
        return [row["prescription"].status for row in rows]

    def test_6_1_the_counts_cover_every_waiting_status(self):
        self.assertEqual(
            prescription_status_counts(),
            {"all": 4, "received": 1, "preparing": 1, "ready": 2},
        )

    def test_6_1_the_list_holds_every_waiting_prescription(self):
        self.assertCountEqual(
            [row["prescription"] for row in prescriptions_to_prepare()],
            [self.received, self.preparing, self.ready, self.ready_too],
        )

    def test_6_1_the_list_can_show_one_status(self):
        self.assertEqual(self.statuses(prescriptions_to_prepare("ready")), ["ready", "ready"])
        self.assertEqual(self.statuses(prescriptions_to_prepare("received")), ["received"])

    def test_6_1_collected_prescriptions_are_never_listed(self):
        self.assertEqual(prescriptions_to_prepare("collected"), [])

    def test_6_1_stock_alerts_list_low_stock_then_near_expiry(self):
        alerts = [(alert["medicine"], alert["kind"]) for alert in stock_alerts()]
        self.assertEqual(alerts, [(self.amoxicillin, "low"), (self.ibuprofen, "expiring")])

    def test_6_1_a_low_stock_alert_knows_what_is_left(self):
        alert = stock_alerts()[0]
        self.assertEqual(alert["available"], 8)

    def test_6_1_a_near_expiry_alert_knows_its_batch_and_days_left(self):
        alert = stock_alerts()[1]
        self.assertEqual(alert["batch"].batch_number, "IBU-001")
        self.assertEqual(alert["days_left"], 30)


class DashboardPageAccessTests(DashboardTestData):
    def test_6_1_signed_out_visitors_go_to_the_staff_login(self):
        self.assertRedirects(self.client.get("/dashboard/"), "/login/staff/?next=/dashboard/")

    def test_6_1_patient_side_users_are_refused(self):
        self.client.force_login(self.maya)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_6_1_every_staff_role_can_open_the_dashboard(self):
        for user in [self.nadim, self.rana, self.karim]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_6_1_the_menu_links_to_the_dashboard(self):
        self.client.force_login(self.rana)
        self.assertContains(self.client.get("/prescriptions/"), 'href="/dashboard/"')


class DashboardPageTests(DashboardTestData):
    def setUp(self):
        self.client.force_login(self.rana)

    def test_6_1_page_uses_the_figma_wording(self):
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Today's Overview")
        self.assertContains(response, "Everything that needs your attention today, in one place.")
        self.assertContains(response, "Prescriptions to Prepare")
        self.assertContains(response, "Stock Alerts")

    def test_6_1_the_cards_show_the_waiting_counts(self):
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Pending prescriptions")
        self.assertContains(response, "2 ready to collect")
        self.assertContains(response, "Low stock")
        self.assertContains(response, "Below minimum")
        self.assertContains(response, "Near expiry")
        self.assertContains(response, "Within 90 days")

    def test_6_1_each_card_opens_its_full_list(self):
        response = self.client.get("/dashboard/")
        self.assertContains(response, 'href="/prescriptions/"')
        self.assertContains(response, 'href="/inventory/?show=low_stock"')
        self.assertContains(response, 'href="/inventory/?show=expiring"')

    def test_6_1_the_status_pills_show_their_counts(self):
        response = self.client.get("/dashboard/")
        for words in ["All 4", "Received 1", "Preparing 1", "Ready 2"]:
            self.assertContains(response, words)

    def test_6_1_the_table_lists_the_waiting_prescriptions(self):
        response = self.client.get("/dashboard/")
        for prescription in [self.received, self.preparing, self.ready, self.ready_too]:
            self.assertContains(response, f'href="/prescriptions/{prescription.pk}/"')
        for prescription in [self.collected, self.returned]:
            self.assertNotContains(response, f'href="/prescriptions/{prescription.pk}/"')

    def test_6_1_a_pill_shows_only_its_status(self):
        response = self.client.get("/dashboard/", {"status": "ready"})
        self.assertContains(response, f'href="/prescriptions/{self.ready.pk}/"')
        self.assertNotContains(response, f'href="/prescriptions/{self.received.pk}/"')
        self.assertContains(response, 'href="/dashboard/?status=ready" class="selected"')

    def test_6_1_view_all_opens_the_prescriptions_page(self):
        self.assertContains(self.client.get("/dashboard/"), "View all 4 prescriptions")

    def test_6_1_stock_alerts_say_what_is_wrong(self):
        response = self.client.get("/dashboard/")
        self.assertContains(response, "8 left, minimum is 20")
        self.assertContains(response, "Batch IBU-001, expires in 30 days")
        self.assertContains(response, "Open inventory")
        self.assertContains(response, 'href="/inventory/"')

    def test_6_1_an_assistant_sees_the_same_dashboard(self):
        self.client.force_login(self.karim)
        self.assertContains(self.client.get("/dashboard/"), "Today's Overview")


class EmptyDashboardTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create(username="rana", role="pharmacist"))

    def test_6_1_no_waiting_prescriptions_says_so(self):
        self.assertContains(self.client.get("/dashboard/"), "No prescriptions waiting.")

    def test_6_1_no_stock_alerts_says_so(self):
        self.assertContains(self.client.get("/dashboard/"), "No stock alerts.")
