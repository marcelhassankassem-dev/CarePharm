import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from assistant.agent import MAX_STEPS, MODEL, gemini_client, run_agent, system_prompt
from assistant.services import ask_assistant, unavailable_message
from assistant.tools import (
    TOOL_DECLARATIONS,
    TOOL_FUNCTIONS,
    check_stock,
    pharmacy_info,
    prescription_status,
    request_refill,
    reserve_medicine,
)
from assistant.views import QUICK_OPTIONS
from core.services import pharmacy_details
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Allergy, Patient
from prescriptions.models import Prescription, PrescriptionItem
from reservations.models import RefillRequest, Reservation

HOURS_QUESTION = "What are your opening hours and where are you?"

STUCK_MESSAGE = "I got stuck working on that. Please try rephrasing."


def answer(text, interaction_id):
    return SimpleNamespace(id=interaction_id, steps=[SimpleNamespace(type="model_output")], output_text=text)


def tool_call(name, arguments, call_id, interaction_id):
    step = SimpleNamespace(type="function_call", name=name, arguments=arguments, id=call_id)
    return SimpleNamespace(id=interaction_id, steps=[step], output_text="")


class FakeGeminiTestCase(SimpleTestCase):
    def setUp(self):
        self.user = get_user_model()(username="maya", role="patient")
        self.gemini = MagicMock()
        patcher = patch("assistant.agent.gemini_client", return_value=self.gemini)
        patcher.start()
        self.addCleanup(patcher.stop)

    def replies(self, *interactions):
        self.gemini.interactions.create.side_effect = list(interactions)

    def sent(self, call_number):
        return self.gemini.interactions.create.call_args_list[call_number].kwargs


class RunAgentTests(FakeGeminiTestCase):
    def test_t5_a_plain_answer_comes_back_with_its_interaction_id(self):
        self.replies(answer("We open at 8:00.", "first"))
        self.assertEqual(run_agent(self.user, "When do you open?"), ("We open at 8:00.", "first"))

    def test_t5_gemini_gets_the_message_the_rules_and_the_tools(self):
        self.replies(answer("We open at 8:00.", "first"))
        run_agent(self.user, "When do you open?")
        request = self.sent(0)
        self.assertEqual(request["model"], MODEL)
        self.assertEqual(request["input"], "When do you open?")
        self.assertEqual(request["system_instruction"], system_prompt())
        self.assertEqual(request["tools"], TOOL_DECLARATIONS)
        self.assertIsNone(request["previous_interaction_id"])

    def test_t5_gemini_is_asked_to_think_as_little_as_possible(self):
        self.replies(answer("We open at 8:00.", "first"))
        run_agent(self.user, "When do you open?")
        self.assertEqual(self.sent(0)["generation_config"], {"thinking_level": "minimal"})

    def test_t5_the_next_message_continues_the_conversation(self):
        self.replies(answer("We are closed on Sunday.", "second"))
        run_agent(self.user, "And on Sunday?", "first")
        self.assertEqual(self.sent(0)["previous_interaction_id"], "first")

    def test_t5_a_tool_call_is_run_and_its_result_sent_back(self):
        self.replies(
            tool_call("pharmacy_info", {}, "call-1", "first"),
            answer("We open Mon to Sat, 8:00 to 22:00.", "second"),
        )
        self.assertEqual(
            run_agent(self.user, "When do you open?"),
            ("We open Mon to Sat, 8:00 to 22:00.", "second"),
        )
        follow_up = self.sent(1)
        self.assertEqual(follow_up["previous_interaction_id"], "first")
        self.assertEqual(
            follow_up["input"],
            [
                {
                    "type": "function_result",
                    "name": "pharmacy_info",
                    "call_id": "call-1",
                    "result": [{"type": "text", "text": json.dumps(pharmacy_details())}],
                }
            ],
        )
        self.assertEqual(follow_up["generation_config"], {"thinking_level": "minimal"})

    def test_t5_each_tool_gets_the_signed_in_user_and_geminis_arguments(self):
        recorder = MagicMock(return_value={"done": True})
        self.replies(tool_call("pharmacy_info", {"topic": "hours"}, "call-1", "first"), answer("Done.", "second"))
        with patch.dict("assistant.agent.TOOL_FUNCTIONS", {"pharmacy_info": recorder}):
            run_agent(self.user, "When do you open?")
        recorder.assert_called_once_with(self.user, topic="hours")

    def test_t5_a_gemini_that_keeps_calling_tools_is_stopped(self):
        self.replies(*[tool_call("pharmacy_info", {}, f"call-{step}", f"step-{step}") for step in range(MAX_STEPS + 1)])
        reply, interaction_id = run_agent(self.user, "When do you open?")
        self.assertEqual(reply, STUCK_MESSAGE)
        self.assertEqual(self.gemini.interactions.create.call_count, MAX_STEPS + 1)


class ToolTests(SimpleTestCase):
    def test_t5_pharmacy_info_gives_the_pharmacys_details(self):
        self.assertEqual(
            pharmacy_info(get_user_model()(username="maya")),
            {
                "name": settings.PHARMACY_NAME,
                "address": settings.PHARMACY_ADDRESS,
                "phone": settings.PHARMACY_PHONE,
                "hours": settings.PHARMACY_HOURS,
            },
        )

    def test_t5_every_declared_tool_has_a_function(self):
        self.assertEqual({declaration["name"] for declaration in TOOL_DECLARATIONS}, set(TOOL_FUNCTIONS))


class SystemPromptTests(SimpleTestCase):
    def test_t5_the_prompt_names_the_pharmacy_and_its_hours(self):
        prompt = system_prompt()
        self.assertIn(settings.PHARMACY_NAME, prompt)
        self.assertIn(settings.PHARMACY_HOURS, prompt)

    def test_t5_the_prompt_forbids_diagnosing_and_recommending(self):
        prompt = system_prompt().lower()
        self.assertIn("only answer questions about the pharmacy", prompt)
        self.assertIn("never diagnose", prompt)
        self.assertIn("never recommend", prompt)

    def test_t5_the_prompt_says_to_admit_what_it_cannot_answer(self):
        self.assertIn("say clearly that you cannot answer", system_prompt().lower())


class GeminiClientTests(SimpleTestCase):
    def test_t5_the_key_is_a_setting(self):
        self.assertTrue(hasattr(settings, "GEMINI_API_KEY"))

    @override_settings(GEMINI_API_KEY="test-key")
    def test_t5_the_client_is_made_from_the_setting(self):
        with patch("assistant.agent.genai.Client") as client_class:
            gemini_client()
        self.assertEqual(client_class.call_args.kwargs["api_key"], "test-key")

    def test_9_1a_gemini_is_given_up_on_after_30_seconds(self):
        with patch("assistant.agent.genai.Client") as client_class:
            gemini_client()
        self.assertEqual(client_class.call_args.kwargs["http_options"], {"timeout": 30000})


class AskAssistantTests(SimpleTestCase):
    def setUp(self):
        self.user = get_user_model()(username="samira", role="patient")
        patcher = patch("assistant.services.run_agent", return_value=("We open at 8:00.", "first"))
        self.run_agent = patcher.start()
        self.addCleanup(patcher.stop)

    def test_9_1a_the_assistants_answer_and_interaction_id_come_back(self):
        self.assertEqual(ask_assistant(self.user, "When do you open?", None), ("We open at 8:00.", "first"))
        self.run_agent.assert_called_once_with(self.user, "When do you open?", None)

    def test_9_1a_the_conversation_continues_from_the_last_interaction(self):
        ask_assistant(self.user, "And on Sunday?", "first")
        self.run_agent.assert_called_once_with(self.user, "And on Sunday?", "first")

    def test_9_1a_when_gemini_fails_the_patient_is_told_clearly(self):
        self.run_agent.side_effect = Exception("503 high demand")
        self.assertEqual(ask_assistant(self.user, "When do you open?", "first"), (unavailable_message(), "first"))

    def test_9_1a_the_unavailable_message_gives_the_pharmacys_phone(self):
        message = unavailable_message()
        self.assertIn("not available right now", message)
        self.assertIn(settings.PHARMACY_PHONE, message)


class QuickOptionTests(SimpleTestCase):
    def test_9_1a_the_quick_options_are_the_ones_in_the_figma_frame(self):
        self.assertEqual(
            [option["label"] for option in QUICK_OPTIONS],
            [
                "Find a medicine",
                "Check prescription status",
                "Request a refill",
                "Pharmacy opening hours",
                "General questions",
            ],
        )

    def test_9_1a_the_opening_hours_option_asks_for_hours_and_location(self):
        hours_option = next(option for option in QUICK_OPTIONS if option["label"] == "Pharmacy opening hours")
        self.assertEqual(hours_option["question"], HOURS_QUESTION)


class ChatPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira = User.objects.create_user(username="samira", password="pass", role="patient")
        Patient.objects.create(name="Samira Haddad", date_of_birth="1958-03-14", user=cls.samira)
        cls.rana = User.objects.create_user(username="rana", password="pass", role="pharmacist")

    def setUp(self):
        self.client.force_login(self.samira)
        patcher = patch("assistant.services.run_agent", return_value=("We open at 8:00.", "first"))
        self.run_agent = patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, message):
        return self.client.post("/assistant/", {"message": message})

    def test_9_1a_signed_out_visitors_go_to_the_patient_sign_in(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/assistant/"), "/login/?next=/assistant/")

    def test_9_1a_staff_cannot_open_the_patients_chat(self):
        self.client.force_login(self.rana)
        self.assertEqual(self.client.get("/assistant/").status_code, 403)

    def test_9_1a_an_empty_chat_shows_the_welcome_and_the_quick_options(self):
        response = self.client.get("/assistant/")
        self.assertContains(response, "CarePharm assistant")
        self.assertContains(response, "Hi, I am your CarePharm assistant")
        self.assertContains(response, "How can I help you today?")
        for option in QUICK_OPTIONS:
            self.assertContains(response, option["label"])

    def test_9_1a_each_quick_option_sends_its_question(self):
        response = self.client.get("/assistant/")
        self.assertContains(response, f'name="message" value="{HOURS_QUESTION}"')

    def test_9_1a_sending_asks_the_assistant_and_comes_back_to_the_chat(self):
        response = self.send(HOURS_QUESTION)
        self.assertRedirects(response, "/assistant/")
        self.run_agent.assert_called_once_with(self.samira, HOURS_QUESTION, None)

    def test_9_1a_the_question_and_the_answer_are_shown_in_order(self):
        page = self.client.get(self.send(HOURS_QUESTION).url).content.decode()
        self.assertIn(HOURS_QUESTION, page)
        self.assertIn("We open at 8:00.", page)
        self.assertLess(page.index(HOURS_QUESTION), page.index("We open at 8:00."))

    def test_9_1a_answers_keep_their_line_breaks(self):
        self.run_agent.return_value = ("Mon to Sat.\nBliss Street.", "first")
        self.send("When and where?")
        self.assertContains(self.client.get("/assistant/"), "Mon to Sat.<br>Bliss Street.")

    def test_9_1a_the_next_message_continues_the_same_conversation(self):
        self.send(HOURS_QUESTION)
        self.run_agent.return_value = ("We are closed on Sunday.", "second")
        self.send("And on Sunday?")
        self.assertEqual(self.run_agent.call_args.args, (self.samira, "And on Sunday?", "first"))
        page = self.client.get("/assistant/").content.decode()
        self.assertLess(page.index("We open at 8:00."), page.index("We are closed on Sunday."))

    def test_9_1a_quick_options_are_hidden_once_the_chat_has_started(self):
        self.send(HOURS_QUESTION)
        self.assertNotContains(self.client.get("/assistant/"), "Check prescription status")

    def test_9_1a_an_empty_message_is_ignored(self):
        self.send("   ")
        self.run_agent.assert_not_called()
        self.assertContains(self.client.get("/assistant/"), "How can I help you today?")

    def test_9_1a_when_gemini_fails_the_chat_says_the_assistant_is_unavailable(self):
        self.run_agent.side_effect = Exception("503 high demand")
        self.send(HOURS_QUESTION)
        self.assertContains(self.client.get("/assistant/"), "not available right now")

    def test_9_1a_start_over_clears_the_chat_but_keeps_the_patient_signed_in(self):
        self.send(HOURS_QUESTION)
        self.assertRedirects(self.client.get("/assistant/start-over/"), "/assistant/")
        response = self.client.get("/assistant/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "We open at 8:00.")
        self.assertContains(response, "How can I help you today?")

    def test_9_1a_start_over_begins_a_new_gemini_conversation(self):
        self.send(HOURS_QUESTION)
        self.client.get("/assistant/start-over/")
        self.send("When do you open?")
        self.assertIsNone(self.run_agent.call_args.args[2])

    def test_9_1a_the_close_button_goes_back_home(self):
        self.assertContains(self.client.get("/assistant/"), 'href="/home/" class="chat-close"')

    def test_9_1a_the_home_card_opens_the_assistant(self):
        self.assertContains(self.client.get("/home/"), 'href="/assistant/" class="home-card assistant"')

    def test_9_1a_the_patient_menu_links_to_the_assistant(self):
        self.assertContains(self.client.get("/notifications/"), 'href="/assistant/"')


def make_medicine(name, strength, barcode, class_name, price):
    drug_class, created = DrugClass.objects.get_or_create(name=class_name)
    return Medicine.objects.create(
        name=name,
        strength=strength,
        barcode=barcode,
        drug_class=drug_class,
        price=Decimal(price),
        minimum_quantity=5,
    )


def make_batch(medicine, batch_number, quantity):
    return Batch.objects.create(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=timezone.localdate() + timedelta(days=300),
        quantity=quantity,
        unit_cost=Decimal("2.00"),
        supplier="Cedar Medical Supplies",
    )


def make_prescription(patient, medicine, status, **dates):
    prescription = Prescription.objects.create(patient=patient, status=status, **dates)
    PrescriptionItem.objects.create(prescription=prescription, medicine=medicine, quantity=30, days_of_supply=30)
    return prescription


def collected_long_term_item(patient, medicine, days_of_supply):
    prescription = Prescription.objects.create(patient=patient, status="collected", collected_at=timezone.now())
    return PrescriptionItem.objects.create(
        prescription=prescription,
        medicine=medicine,
        quantity=30,
        days_of_supply=days_of_supply,
        long_term=True,
    )


def day(moment):
    return timezone.localtime(moment).strftime("%d %b %Y")


class PharmacyDataTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.samira_user = User.objects.create(username="samira", role="patient")
        cls.samira = Patient.objects.create(
            name="Samira Haddad",
            date_of_birth=date(1958, 3, 14),
            phone="03 100 001",
            user=cls.samira_user,
        )
        cls.maya_user = User.objects.create(username="maya", role="patient")
        cls.maya = Patient.objects.create(name="Maya Khoury", date_of_birth=date(1990, 6, 2), user=cls.maya_user)
        cls.omar = User.objects.create(username="omar", role="patient")
        cls.metformin_500 = make_medicine("Metformin", "500 mg", "1000000000001", "Biguanides", "7.50")
        cls.metformin_850 = make_medicine("Metformin", "850 mg", "1000000000002", "Biguanides", "9.00")
        cls.ibuprofen = make_medicine("Ibuprofen", "400 mg", "1000000000003", "NSAIDs", "3.25")
        penicillins = DrugClass.objects.create(name="Penicillins")
        Allergy.objects.create(patient=cls.samira, drug_class=penicillins)

    def setUp(self):
        self.metformin_500_batch = make_batch(self.metformin_500, "MET-500", 20)
        self.metformin_850_batch = make_batch(self.metformin_850, "MET-850", 5)

    def quantity_of(self, batch):
        batch.refresh_from_db()
        return batch.quantity


class CheckStockToolTests(PharmacyDataTestCase):
    def test_9_1b_check_stock_gives_the_price_and_how_many_are_in_stock(self):
        self.assertEqual(
            check_stock(self.samira_user, "Metformin 500 mg"),
            {
                "found": True,
                "medicines": [
                    {"medicine": "Metformin 500 mg", "price_usd": "7.50", "in_stock": 20, "status": "Available"}
                ],
            },
        )

    def test_9_1b_check_stock_says_when_there_are_not_enough(self):
        line = check_stock(self.samira_user, "Metformin 850 mg", 8)["medicines"][0]
        self.assertEqual((line["in_stock"], line["status"]), (5, "Only 5 left"))

    def test_9_1b_check_stock_says_when_a_medicine_is_out_of_stock(self):
        line = check_stock(self.samira_user, "ibuprofen")["medicines"][0]
        self.assertEqual((line["in_stock"], line["status"]), (0, "Out of stock"))

    def test_9_1b_without_a_strength_every_strength_is_given(self):
        lines = check_stock(self.samira_user, "metformin")["medicines"]
        self.assertEqual([line["medicine"] for line in lines], ["Metformin 500 mg", "Metformin 850 mg"])

    def test_9_1b_check_stock_says_when_the_pharmacy_does_not_have_the_medicine(self):
        result = check_stock(self.samira_user, "Aspirin")
        self.assertFalse(result["found"])
        self.assertIn("Aspirin", result["message"])

    def test_9_1b_check_stock_first_returns_expired_reservations_to_stock(self):
        reservation = Reservation.objects.create(
            patient=self.maya, medicine=self.metformin_500, quantity=5, batch=self.metformin_500_batch
        )
        self.metformin_500_batch.quantity = 15
        self.metformin_500_batch.save()
        Reservation.objects.filter(pk=reservation.pk).update(created_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(check_stock(self.samira_user, "Metformin 500 mg")["medicines"][0]["in_stock"], 20)


class ReserveMedicineToolTests(PharmacyDataTestCase):
    def test_9_1b_reserving_takes_the_stock_and_gives_the_pickup_time(self):
        result = reserve_medicine(self.samira_user, "Metformin 500 mg", 2)
        reservation = Reservation.objects.get()
        self.assertEqual(reservation.patient, self.samira)
        self.assertEqual(self.quantity_of(self.metformin_500_batch), 18)
        pick_up_before = timezone.localtime(reservation.created_at + timedelta(hours=2)).strftime("%d %b %Y, %H:%M")
        self.assertEqual(
            result,
            {"reserved": True, "medicine": "Metformin 500 mg", "quantity": 2, "pick_up_before": pick_up_before},
        )

    def test_9_1b_quantities_from_gemini_may_come_as_decimals(self):
        self.assertEqual(reserve_medicine(self.samira_user, "Metformin 500 mg", 2.0)["quantity"], 2)
        self.assertEqual(self.quantity_of(self.metformin_500_batch), 18)

    def test_9_1b_reserving_needs_one_exact_medicine(self):
        result = reserve_medicine(self.samira_user, "metformin", 2)
        self.assertFalse(result["reserved"])
        self.assertEqual(result["choices"], ["Metformin 500 mg", "Metformin 850 mg"])
        self.assertFalse(Reservation.objects.exists())

    def test_9_1b_reserving_a_medicine_the_pharmacy_does_not_have_is_refused(self):
        result = reserve_medicine(self.samira_user, "Aspirin", 1)
        self.assertFalse(result["reserved"])
        self.assertIn("Aspirin", result["message"])

    def test_9_1b_reserving_more_than_one_batch_has_is_refused(self):
        result = reserve_medicine(self.samira_user, "Metformin 850 mg", 8)
        self.assertFalse(result["reserved"])
        self.assertIn("No single batch", result["message"])
        self.assertEqual(self.quantity_of(self.metformin_850_batch), 5)
        self.assertFalse(Reservation.objects.exists())

    def test_9_1b_a_login_without_a_patient_record_cannot_reserve(self):
        self.assertFalse(reserve_medicine(self.omar, "Metformin 500 mg", 2)["reserved"])
        self.assertFalse(Reservation.objects.exists())


class PrescriptionStatusToolTests(PharmacyDataTestCase):
    def test_9_1b_prescriptions_come_newest_first_with_medicines_status_and_dates(self):
        now = timezone.now()
        collected = make_prescription(
            self.samira, self.metformin_500, "collected", ready_at=now, collected_at=now
        )
        ready = make_prescription(self.samira, self.metformin_850, "ready", ready_at=now)
        self.assertEqual(
            prescription_status(self.samira_user),
            {
                "prescriptions": [
                    {
                        "number": ready.pk,
                        "medicines": ["Metformin 850 mg"],
                        "status": "Ready",
                        "received_on": day(ready.created_at),
                        "ready_on": day(now),
                        "collected_on": None,
                    },
                    {
                        "number": collected.pk,
                        "medicines": ["Metformin 500 mg"],
                        "status": "Collected",
                        "received_on": day(collected.created_at),
                        "ready_on": day(now),
                        "collected_on": day(now),
                    },
                ]
            },
        )

    def test_9_1b_only_the_five_newest_prescriptions_are_given(self):
        for _ in range(6):
            make_prescription(self.samira, self.metformin_500, "received")
        self.assertEqual(len(prescription_status(self.samira_user)["prescriptions"]), 5)

    def test_9_1b_only_the_signed_in_patients_prescriptions_are_given(self):
        make_prescription(self.maya, self.metformin_500, "received")
        self.assertEqual(prescription_status(self.samira_user), {"prescriptions": []})

    def test_9_1b_a_login_without_a_patient_record_has_no_prescriptions(self):
        self.assertEqual(prescription_status(self.omar), {"prescriptions": []})


class RequestRefillToolTests(PharmacyDataTestCase):
    def setUp(self):
        super().setUp()
        self.due_item = collected_long_term_item(self.samira, self.metformin_500, days_of_supply=2)
        self.not_due_item = collected_long_term_item(self.samira, self.metformin_850, days_of_supply=30)
        runs_out_on = timezone.localdate() + timedelta(days=2)
        self.due_refills = [{"medicine": "Metformin 500 mg", "runs_out_on": runs_out_on.strftime("%d %b %Y")}]

    def test_9_1b_a_due_refill_is_requested(self):
        result = request_refill(self.samira_user, "Metformin 500 mg")
        self.assertEqual(
            result,
            {"requested": True, "medicine": "Metformin 500 mg", "status": "Waiting for the pharmacist"},
        )
        refill_request = RefillRequest.objects.get()
        self.assertEqual(refill_request.prescription_item, self.due_item)
        self.assertEqual(refill_request.requested_by, self.samira_user)

    def test_9_1b_without_a_medicine_the_due_refills_are_listed(self):
        result = request_refill(self.samira_user)
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], self.due_refills)
        self.assertFalse(RefillRequest.objects.exists())

    def test_9_1b_a_refill_that_is_not_due_is_refused(self):
        result = request_refill(self.samira_user, "Metformin 850 mg")
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], self.due_refills)
        self.assertFalse(RefillRequest.objects.exists())

    def test_9_1b_a_refill_is_not_requested_twice(self):
        request_refill(self.samira_user, "Metformin 500 mg")
        result = request_refill(self.samira_user, "Metformin 500 mg")
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], [])
        self.assertEqual(RefillRequest.objects.count(), 1)

    def test_9_1b_a_login_without_a_patient_record_cannot_request_a_refill(self):
        self.assertFalse(request_refill(self.omar, "Metformin 500 mg")["requested"])
        self.assertFalse(RefillRequest.objects.exists())


class ToolPrivacyTests(PharmacyDataTestCase):
    def test_9_1b_no_tool_result_contains_the_patients_name_phone_birth_date_or_allergies(self):
        collected_long_term_item(self.samira, self.metformin_500, days_of_supply=2)
        make_prescription(self.samira, self.metformin_850, "ready", ready_at=timezone.now())
        results = [
            pharmacy_info(self.samira_user),
            check_stock(self.samira_user, "metformin"),
            reserve_medicine(self.samira_user, "Metformin 850 mg", 1),
            prescription_status(self.samira_user),
            request_refill(self.samira_user),
            request_refill(self.samira_user, "Metformin 500 mg"),
        ]
        sent_to_gemini = json.dumps(results)
        for private in ["Samira", "Haddad", "03 100 001", "1958", "Penicillins"]:
            self.assertNotIn(private, sent_to_gemini)


class ToolDeclarationTests(SimpleTestCase):
    def test_9_1b_gemini_is_told_about_the_pharmacy_data_tools(self):
        self.assertEqual(
            [declaration["name"] for declaration in TOOL_DECLARATIONS],
            ["pharmacy_info", "check_stock", "reserve_medicine", "prescription_status", "request_refill"],
        )

    def test_9_1b_the_prompt_asks_to_confirm_before_reserving_or_requesting_a_refill(self):
        prompt = system_prompt().lower()
        self.assertIn("before reserving a medicine or requesting a refill", prompt)
        self.assertIn("ask the patient to confirm", prompt)

    def test_9_1b_the_prompt_asks_for_plain_text_because_the_chat_shows_no_markdown(self):
        self.assertIn("plain text only, without markdown", system_prompt().lower())
