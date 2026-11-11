from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.models import Batch, DrugClass, Medicine
from passport.models import Patient
from prescriptions.models import Prescription
from reports.services import (
    best_day,
    best_sellers,
    daily_sales,
    expired_stock_loss,
    monthly_expiry_losses,
    period_expiry_loss,
    prescription_status_counts,
    prescriptions_to_prepare,
    report_period,
    sales_summary,
    stock_alerts,
)
from sales.models import Sale
from sales.services import record_sale, sold_lines_between


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


def priced_medicine(name, barcode, price):
    drug_class, created = DrugClass.objects.get_or_create(name="Test class")
    return Medicine.objects.create(
        name=name,
        strength="500 mg",
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal(price),
        minimum_quantity=10,
    )


def sell(staff, items, day, source="counter", patient=None):
    sale = record_sale(staff, "cash", items, source, patient)
    Sale.objects.filter(pk=sale.pk).update(time=timezone.make_aware(datetime.combine(day, time(12, 0))))
    return sale


class ReportPeriodTests(TestCase):
    def period(self, key, today=date(2026, 11, 11)):
        found = report_period(key, today)
        return found["key"], found["label"], found["start"], found["end"]

    def test_7_1_today_is_one_day(self):
        self.assertEqual(self.period("today"), ("today", "Today", date(2026, 11, 11), date(2026, 11, 11)))

    def test_7_1_this_month_runs_from_the_first_to_today(self):
        self.assertEqual(self.period("month"), ("month", "This month", date(2026, 11, 1), date(2026, 11, 11)))

    def test_7_1_last_month_is_the_whole_previous_month(self):
        self.assertEqual(
            self.period("last_month"),
            ("last_month", "Last month", date(2026, 10, 1), date(2026, 10, 31)),
        )

    def test_7_1_last_month_in_january_is_december(self):
        self.assertEqual(
            self.period("last_month", today=date(2027, 1, 5)),
            ("last_month", "Last month", date(2026, 12, 1), date(2026, 12, 31)),
        )

    def test_7_1_an_unknown_period_means_this_month(self):
        self.assertEqual(self.period("year")[0], "month")


class SalesReportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.karim = get_user_model().objects.create(username="karim", role="assistant")
        samira = Patient.objects.create(name="Samira", date_of_birth=date(1958, 3, 14))
        cls.paracetamol = priced_medicine("Paracetamol", "1000000000005", "3.00")
        cls.cetirizine = priced_medicine("Cetirizine", "1000000000011", "5.00")
        cls.aspirin = priced_medicine("Aspirin", "1000000000014", "3.50")
        sell(cls.karim, [(cls.paracetamol, 4)], date(2026, 11, 2))
        sell(cls.karim, [(cls.cetirizine, 2), (cls.paracetamol, 1)], date(2026, 11, 5))
        sell(cls.karim, [(cls.aspirin, 2)], date(2026, 11, 5), source="prescription", patient=samira)
        sell(cls.karim, [(cls.aspirin, 50)], date(2026, 10, 20))

    def lines(self):
        return sold_lines_between(date(2026, 11, 1), date(2026, 11, 6))

    def test_7_1_the_total_adds_every_line_of_every_source(self):
        self.assertEqual(sales_summary(self.lines())["total"], Decimal("32.00"))

    def test_7_1_the_total_keeps_the_price_of_the_day_of_the_sale(self):
        self.paracetamol.price = Decimal("9.00")
        self.paracetamol.save()
        self.assertEqual(sales_summary(self.lines())["total"], Decimal("32.00"))

    def test_7_1_the_summary_counts_sales_not_lines(self):
        self.assertEqual(sales_summary(self.lines())["sale_count"], 3)

    def test_7_1_a_period_with_no_sales_adds_up_to_zero(self):
        self.assertEqual(sales_summary([]), {"total": Decimal("0"), "sale_count": 0})

    def test_7_1_there_is_one_bar_per_day_of_the_period(self):
        days = daily_sales(self.lines(), date(2026, 11, 1), date(2026, 11, 6))
        self.assertEqual([day["day"] for day in days], [date(2026, 11, number) for number in range(1, 7)])

    def test_7_1_each_day_has_its_total_and_a_height_against_the_best_day(self):
        days = {day["day"].day: day for day in daily_sales(self.lines(), date(2026, 11, 1), date(2026, 11, 6))}
        self.assertEqual((days[1]["total"], days[1]["height"]), (Decimal("0"), 0))
        self.assertEqual((days[2]["total"], days[2]["height"]), (Decimal("12.00"), 60))
        self.assertEqual((days[5]["total"], days[5]["height"]), (Decimal("20.00"), 100))

    def test_7_1_the_best_day_is_the_one_that_sold_most(self):
        days = daily_sales(self.lines(), date(2026, 11, 1), date(2026, 11, 6))
        self.assertEqual(best_day(days)["day"], date(2026, 11, 5))

    def test_7_1_a_period_with_no_sales_has_no_best_day(self):
        self.assertIsNone(best_day(daily_sales([], date(2026, 11, 1), date(2026, 11, 6))))

    def test_7_1_best_sellers_are_ranked_by_units_then_name(self):
        ranked = [(row["medicine"], row["units"], row["width"]) for row in best_sellers(self.lines())]
        self.assertEqual(
            ranked,
            [(self.paracetamol, 5, 100), (self.aspirin, 2, 40), (self.cetirizine, 2, 40)],
        )

    def test_7_1_only_the_top_ones_are_kept(self):
        self.assertEqual(len(best_sellers(self.lines(), limit=2)), 2)

    def test_7_1_a_period_with_no_sales_has_no_best_sellers(self):
        self.assertEqual(best_sellers([]), [])


class SalesReportPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.nadim = User.objects.create(username="nadim", role="owner", is_pharmacist=True)
        cls.rana = User.objects.create(username="rana", role="pharmacist")
        cls.karim = User.objects.create(username="karim", role="assistant")
        cls.maya = User.objects.create(username="maya", role="patient")
        cls.today = timezone.localdate()
        last_month = (cls.today.replace(day=1) - timedelta(days=1)).replace(day=1)
        paracetamol = priced_medicine("Paracetamol", "1000000000005", "3.00")
        aspirin = priced_medicine("Aspirin", "1000000000014", "3.50")
        sell(cls.karim, [(paracetamol, 2)], cls.today)
        sell(cls.karim, [(aspirin, 4)], last_month)

    def setUp(self):
        self.client.force_login(self.nadim)

    def test_7_1_signed_out_visitors_go_to_the_staff_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/reports/"), "/login/staff/?next=/reports/")

    def test_7_1_only_the_owner_can_open_reports(self):
        for user in [self.rana, self.karim, self.maya]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/reports/").status_code, 403)

    def test_7_1_the_menu_links_to_reports_for_the_owner_only(self):
        self.assertContains(self.client.get("/reports/"), 'href="/reports/"')
        self.client.force_login(self.rana)
        self.assertNotContains(self.client.get("/dashboard/"), 'href="/reports/"')

    def test_7_1_page_uses_the_figma_wording(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "How the pharmacy is doing this month, in simple numbers.")
        self.assertContains(response, "Daily Sales")
        self.assertContains(response, "Most Sold")
        self.assertContains(response, "Units sold this month")

    def test_7_1_the_period_pills_link_to_each_period(self):
        response = self.client.get("/reports/")
        self.assertContains(response, 'href="/reports/?period=today"')
        self.assertContains(response, 'href="/reports/?period=month" class="selected"')
        self.assertContains(response, 'href="/reports/?period=last_month"')

    def test_7_1_the_cards_show_this_month_and_today(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "Sales this month")
        self.assertContains(response, "$6.00")
        self.assertContains(response, "1 sale so far")

    def test_7_1_most_sold_lists_the_best_sellers(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "1. Paracetamol 500 mg")
        self.assertContains(response, "2 sold")

    def test_7_1_the_best_day_is_named(self):
        best = f"Best day: {self.today:%b} {self.today.day} · $6.00"
        self.assertContains(self.client.get("/reports/"), best)

    def test_7_1_last_month_shows_only_last_month(self):
        response = self.client.get("/reports/", {"period": "last_month"})
        self.assertContains(response, "How the pharmacy is doing last month, in simple numbers.")
        self.assertContains(response, "Sales last month")
        self.assertContains(response, "$14.00")
        self.assertContains(response, "1. Aspirin 500 mg")
        self.assertNotContains(response, "1. Paracetamol 500 mg")
        self.assertContains(response, 'href="/reports/?period=last_month" class="selected"')


class EmptySalesReportTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create(username="nadim", role="owner"))

    def test_7_1_no_sales_says_so(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "No sales in this period.")
        self.assertContains(response, "$0.00")
        self.assertNotContains(response, "Best day")

    def test_7_2_no_expired_stock_shows_zero(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "Lost to expired stock")
        self.assertContains(response, "0 items this month")


def batch_expiring_on(medicine, batch_number, quantity, unit_cost, expiry_date):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=expiry_date,
        quantity=quantity,
        unit_cost=Decimal(unit_cost),
        supplier="Cedar Medical Supplies",
    )


def loss_numbers(loss):
    return loss["month"], loss["items"], loss["value"]


class ExpiredStockServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = date(2026, 11, 11)
        paracetamol = priced_medicine("Paracetamol", "1000000000005", "3.00")
        cetirizine = priced_medicine("Cetirizine", "1000000000011", "5.00")
        amoxicillin = priced_medicine("Amoxicillin", "1000000000001", "8.50")
        batch_expiring_on(paracetamol, "PAR-001", 10, "1.50", date(2026, 11, 3))
        batch_expiring_on(cetirizine, "CET-001", 4, "2.50", date(2026, 11, 10))
        batch_expiring_on(cetirizine, "CET-002", 6, "2.50", date(2026, 11, 11))
        batch_expiring_on(amoxicillin, "AMX-001", 8, "4.25", date(2026, 10, 31))
        batch_expiring_on(amoxicillin, "AMX-002", 2, "4.25", date(2026, 10, 1))
        batch_expiring_on(paracetamol, "PAR-002", 20, "1.50", date(2026, 9, 15))
        batch_expiring_on(paracetamol, "PAR-003", 30, "1.50", date(2026, 8, 20))
        batch_expiring_on(paracetamol, "PAR-004", 200, "1.50", date(2027, 6, 30))

    def test_7_2_the_loss_is_quantity_times_unit_cost(self):
        loss = expired_stock_loss(date(2026, 10, 1), date(2026, 10, 31), self.today)
        self.assertEqual(loss_numbers(loss), (date(2026, 10, 1), 10, Decimal("42.50")))

    def test_7_2_stock_expiring_today_is_not_lost_yet(self):
        loss = expired_stock_loss(date(2026, 11, 1), date(2026, 11, 11), self.today)
        self.assertEqual(loss_numbers(loss), (date(2026, 11, 1), 14, Decimal("25.00")))

    def test_7_2_a_month_with_nothing_expired_is_zero(self):
        loss = expired_stock_loss(date(2026, 7, 1), date(2026, 7, 31), self.today)
        self.assertEqual(loss_numbers(loss), (date(2026, 7, 1), 0, Decimal("0")))

    def test_7_2_this_month_and_today_show_this_month_so_far(self):
        for key in ["month", "today"]:
            loss = period_expiry_loss(report_period(key, self.today), self.today)
            self.assertEqual(loss_numbers(loss), (date(2026, 11, 1), 14, Decimal("25.00")))

    def test_7_2_last_month_shows_the_whole_of_last_month(self):
        loss = period_expiry_loss(report_period("last_month", self.today), self.today)
        self.assertEqual(loss_numbers(loss), (date(2026, 10, 1), 10, Decimal("42.50")))

    def test_7_2_the_table_shows_this_month_and_the_two_before(self):
        rows = [loss_numbers(loss) for loss in monthly_expiry_losses(self.today)]
        self.assertEqual(
            rows,
            [
                (date(2026, 11, 1), 14, Decimal("25.00")),
                (date(2026, 10, 1), 10, Decimal("42.50")),
                (date(2026, 9, 1), 20, Decimal("30.00")),
            ],
        )

    def test_7_2_the_table_goes_back_across_the_new_year(self):
        months = [loss["month"] for loss in monthly_expiry_losses(date(2027, 1, 5))]
        self.assertEqual(months, [date(2027, 1, 1), date(2026, 12, 1), date(2026, 11, 1)])


class ExpiredStockPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nadim = get_user_model().objects.create(username="nadim", role="owner", is_pharmacist=True)
        cls.today = timezone.localdate()
        cls.last_month_end = cls.today.replace(day=1) - timedelta(days=1)
        amoxicillin = priced_medicine("Amoxicillin", "1000000000001", "8.50")
        batch_expiring_on(amoxicillin, "AMX-001", 8, "4.25", cls.last_month_end)
        batch_expiring_on(amoxicillin, "AMX-002", 50, "4.25", cls.today)

    def setUp(self):
        self.client.force_login(self.nadim)

    def test_7_2_the_card_shows_this_month_by_default(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "Lost to expired stock")
        self.assertContains(response, "0 items this month")

    def test_7_2_the_card_follows_last_month(self):
        response = self.client.get("/reports/", {"period": "last_month"})
        self.assertContains(response, "$34.00")
        self.assertContains(response, "8 items last month")

    def test_7_2_the_table_uses_the_figma_wording(self):
        response = self.client.get("/reports/")
        self.assertContains(response, "Expired Stock Losses")
        self.assertContains(response, "Value of medicines that expired before being sold")

    def test_7_2_the_table_lists_this_month_so_far_and_last_month(self):
        response = self.client.get("/reports/")
        self.assertContains(response, f"{self.today:%B} (so far)")
        self.assertContains(response, f"{self.last_month_end:%B}")
        self.assertContains(response, "$34.00")
