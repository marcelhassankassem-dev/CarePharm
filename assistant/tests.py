import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import CaregiverLink
from assistant.agent import MAX_STEPS, MODEL, gemini_client, run_agent, system_prompt
from assistant.models import PharmacistQuestion
from assistant.questions import (
    answer_question,
    answered_question_count,
    answered_questions,
    pass_to_pharmacist,
    questions_asked_count,
    questions_of,
    waiting_question_count,
    waiting_questions,
)
from assistant.services import ask_assistant, cannot_answer_message, unavailable_message
from assistant.tools import (
    NO_PATIENT_RECORD,
    TOOL_DECLARATIONS,
    TOOL_FUNCTIONS,
    ask_pharmacist,
    check_stock,
    pharmacy_info,
    prescription_status,
    request_refill,
    reserve_medicine,
)
from assistant.views import QUICK_OPTIONS
from core.models import Notification
from core.services import pharmacy_details
from inventory.models import Batch, DrugClass, Medicine
from passport.models import Allergy, Patient
from prescriptions.models import Prescription, PrescriptionItem
from reservations.models import RefillRequest, Reservation

HOURS_QUESTION = "What are your opening hours and where are you?"

GRAPEFRUIT_QUESTION = "Can I take my Amlodipine with grapefruit juice?"

STUCK_MESSAGE = "I got stuck working on that. Please try rephrasing."


def answer(text, interaction_id):
    return SimpleNamespace(id=interaction_id, steps=[SimpleNamespace(type="model_output")], output_text=text)


def tool_call(name, arguments, call_id, interaction_id):
    step = SimpleNamespace(type="function_call", name=name, arguments=arguments, id=call_id)
    return SimpleNamespace(id=interaction_id, steps=[step], output_text="")


class FakeGeminiTestCase(SimpleTestCase):
    def setUp(self):
        self.user = get_user_model()(username="maya", role="patient")
        self.patient = Patient(name="Maya Khoury")
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
        self.assertEqual(run_agent(self.user, self.patient, "When do you open?"), ("We open at 8:00.", "first"))

    def test_t5_gemini_gets_the_message_the_rules_and_the_tools(self):
        self.replies(answer("We open at 8:00.", "first"))
        run_agent(self.user, self.patient, "When do you open?")
        request = self.sent(0)
        self.assertEqual(request["model"], MODEL)
        self.assertEqual(request["input"], "When do you open?")
        self.assertEqual(request["system_instruction"], system_prompt())
        self.assertEqual(request["tools"], TOOL_DECLARATIONS)
        self.assertIsNone(request["previous_interaction_id"])

    def test_t5_gemini_is_asked_to_think_as_little_as_possible(self):
        self.replies(answer("We open at 8:00.", "first"))
        run_agent(self.user, self.patient, "When do you open?")
        self.assertEqual(self.sent(0)["generation_config"], {"thinking_level": "minimal"})

    def test_t5_the_next_message_continues_the_conversation(self):
        self.replies(answer("We are closed on Sunday.", "second"))
        run_agent(self.user, self.patient, "And on Sunday?", "first")
        self.assertEqual(self.sent(0)["previous_interaction_id"], "first")

    def test_t5_a_tool_call_is_run_and_its_result_sent_back(self):
        self.replies(
            tool_call("pharmacy_info", {}, "call-1", "first"),
            answer("We open Mon to Sat, 8:00 to 22:00.", "second"),
        )
        self.assertEqual(
            run_agent(self.user, self.patient, "When do you open?"),
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

    def test_12_1_each_tool_gets_the_user_the_selected_patient_and_geminis_arguments(self):
        recorder = MagicMock(return_value={"done": True})
        self.replies(tool_call("pharmacy_info", {"topic": "hours"}, "call-1", "first"), answer("Done.", "second"))
        with patch.dict("assistant.agent.TOOL_FUNCTIONS", {"pharmacy_info": recorder}):
            run_agent(self.user, self.patient, "When do you open?")
        recorder.assert_called_once_with(self.user, self.patient, topic="hours")

    def test_t5_a_gemini_that_keeps_calling_tools_is_stopped(self):
        self.replies(*[tool_call("pharmacy_info", {}, f"call-{step}", f"step-{step}") for step in range(MAX_STEPS + 1)])
        reply, interaction_id = run_agent(self.user, self.patient, "When do you open?")
        self.assertEqual(reply, STUCK_MESSAGE)
        self.assertEqual(self.gemini.interactions.create.call_count, MAX_STEPS + 1)


class ToolTests(SimpleTestCase):
    def test_t5_pharmacy_info_gives_the_pharmacys_details(self):
        self.assertEqual(
            pharmacy_info(get_user_model()(username="maya"), None),
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
        self.patient = Patient(name="Samira Haddad")
        patcher = patch("assistant.services.run_agent", return_value=("We open at 8:00.", "first"))
        self.run_agent = patcher.start()
        self.addCleanup(patcher.stop)

    def test_9_1a_the_assistants_answer_and_interaction_id_come_back(self):
        self.assertEqual(
            ask_assistant(self.user, self.patient, "When do you open?", None), ("We open at 8:00.", "first")
        )
        self.run_agent.assert_called_once_with(self.user, self.patient, "When do you open?", None)

    def test_9_1a_the_conversation_continues_from_the_last_interaction(self):
        ask_assistant(self.user, self.patient, "And on Sunday?", "first")
        self.run_agent.assert_called_once_with(self.user, self.patient, "And on Sunday?", "first")

    def test_9_1a_when_gemini_fails_the_patient_is_told_clearly(self):
        self.run_agent.side_effect = Exception("503 high demand")
        self.assertEqual(
            ask_assistant(self.user, self.patient, "When do you open?", "first"), (unavailable_message(), "first")
        )

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
        cls.samira_patient = Patient.objects.create(name="Samira Haddad", date_of_birth="1958-03-14", user=cls.samira)
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
        self.run_agent.assert_called_once_with(self.samira, self.samira_patient, HOURS_QUESTION, None)

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
        self.assertEqual(self.run_agent.call_args.args, (self.samira, self.samira_patient, "And on Sunday?", "first"))
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
        self.assertIsNone(self.run_agent.call_args.args[3])

    def test_9_1a_the_close_button_goes_back_home(self):
        self.assertContains(self.client.get("/assistant/"), 'href="/home/" class="chat-close"')

    def test_9_1a_the_home_card_opens_the_assistant(self):
        self.assertContains(self.client.get("/home/"), 'href="/assistant/" class="home-card assistant"')

    def test_9_1a_the_patient_menu_links_to_the_assistant(self):
        self.assertContains(self.client.get("/notifications/"), 'href="/assistant/"')

    def test_9_1c_a_blank_answer_shows_the_cannot_answer_message_in_the_chat(self):
        self.run_agent.return_value = ("", "first")
        self.send("What is the weather today?")
        self.assertContains(self.client.get("/assistant/"), "Sorry, I can&#x27;t answer that.")

    def hand_over(self, user, patient, message, interaction_id):
        PharmacistQuestion.objects.create(patient=patient, asked_by=user, question=message)
        return ("That is a medical question, so I passed it to a pharmacist.", "first")

    def test_9_3_a_passed_on_question_shows_when_to_expect_an_answer(self):
        self.run_agent.side_effect = self.hand_over
        self.send(GRAPEFRUIT_QUESTION)
        page = self.client.get("/assistant/").content.decode()
        self.assertIn("Passed to a pharmacist", page)
        self.assertIn(f"Reply expected during opening hours: {settings.PHARMACY_HOURS}", page)
        self.assertLess(page.index("so I passed it to a pharmacist."), page.index("Passed to a pharmacist"))

    def test_9_3_an_ordinary_answer_shows_no_notice(self):
        self.send(HOURS_QUESTION)
        self.assertNotContains(self.client.get("/assistant/"), "Passed to a pharmacist")

    def test_9_3_the_notice_stays_where_the_question_was_passed_on(self):
        self.run_agent.side_effect = self.hand_over
        self.send(GRAPEFRUIT_QUESTION)
        self.run_agent.side_effect = None
        self.send(HOURS_QUESTION)
        page = self.client.get("/assistant/").content.decode()
        self.assertEqual(page.count("Passed to a pharmacist"), 1)
        self.assertLess(page.index("Passed to a pharmacist"), page.index("We open at 8:00."))

    def test_12_1_the_chat_acts_for_the_selected_patient(self):
        omar = get_user_model().objects.create_user(username="omar", password="pass", role="patient")
        father = Patient.objects.create(name="Khalil Nassar", date_of_birth="1949-11-05")
        CaregiverLink.objects.create(patient=father, caregiver=omar)
        self.client.force_login(omar)
        self.send(HOURS_QUESTION)
        self.assertEqual(self.run_agent.call_args.args[:2], (omar, father))


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
            check_stock(self.samira_user, self.samira, "Metformin 500 mg"),
            {
                "found": True,
                "medicines": [
                    {"medicine": "Metformin 500 mg", "price_usd": "7.50", "in_stock": 20, "status": "Available"}
                ],
            },
        )

    def test_9_1b_check_stock_says_when_there_are_not_enough(self):
        line = check_stock(self.samira_user, self.samira, "Metformin 850 mg", 8)["medicines"][0]
        self.assertEqual((line["in_stock"], line["status"]), (5, "Only 5 left"))

    def test_9_1b_check_stock_says_when_a_medicine_is_out_of_stock(self):
        line = check_stock(self.samira_user, self.samira, "ibuprofen")["medicines"][0]
        self.assertEqual((line["in_stock"], line["status"]), (0, "Out of stock"))

    def test_9_1b_without_a_strength_every_strength_is_given(self):
        lines = check_stock(self.samira_user, self.samira, "metformin")["medicines"]
        self.assertEqual([line["medicine"] for line in lines], ["Metformin 500 mg", "Metformin 850 mg"])

    def test_9_1b_check_stock_says_when_the_pharmacy_does_not_have_the_medicine(self):
        result = check_stock(self.samira_user, self.samira, "Aspirin")
        self.assertFalse(result["found"])
        self.assertIn("Aspirin", result["message"])

    def test_9_1b_check_stock_first_returns_expired_reservations_to_stock(self):
        reservation = Reservation.objects.create(
            patient=self.maya, medicine=self.metformin_500, quantity=5, batch=self.metformin_500_batch
        )
        self.metformin_500_batch.quantity = 15
        self.metformin_500_batch.save()
        Reservation.objects.filter(pk=reservation.pk).update(created_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(check_stock(self.samira_user, self.samira, "Metformin 500 mg")["medicines"][0]["in_stock"], 20)


class ReserveMedicineToolTests(PharmacyDataTestCase):
    def test_9_1b_reserving_takes_the_stock_and_gives_the_pickup_time(self):
        result = reserve_medicine(self.samira_user, self.samira, "Metformin 500 mg", 2)
        reservation = Reservation.objects.get()
        self.assertEqual(reservation.patient, self.samira)
        self.assertEqual(self.quantity_of(self.metformin_500_batch), 18)
        pick_up_before = timezone.localtime(reservation.created_at + timedelta(hours=2)).strftime("%d %b %Y, %H:%M")
        self.assertEqual(
            result,
            {"reserved": True, "medicine": "Metformin 500 mg", "quantity": 2, "pick_up_before": pick_up_before},
        )

    def test_9_1b_quantities_from_gemini_may_come_as_decimals(self):
        self.assertEqual(reserve_medicine(self.samira_user, self.samira, "Metformin 500 mg", 2.0)["quantity"], 2)
        self.assertEqual(self.quantity_of(self.metformin_500_batch), 18)

    def test_9_1b_reserving_needs_one_exact_medicine(self):
        result = reserve_medicine(self.samira_user, self.samira, "metformin", 2)
        self.assertFalse(result["reserved"])
        self.assertEqual(result["choices"], ["Metformin 500 mg", "Metformin 850 mg"])
        self.assertFalse(Reservation.objects.exists())

    def test_9_1b_reserving_a_medicine_the_pharmacy_does_not_have_is_refused(self):
        result = reserve_medicine(self.samira_user, self.samira, "Aspirin", 1)
        self.assertFalse(result["reserved"])
        self.assertIn("Aspirin", result["message"])

    def test_9_1b_reserving_more_than_one_batch_has_is_refused(self):
        result = reserve_medicine(self.samira_user, self.samira, "Metformin 850 mg", 8)
        self.assertFalse(result["reserved"])
        self.assertIn("No single batch", result["message"])
        self.assertEqual(self.quantity_of(self.metformin_850_batch), 5)
        self.assertFalse(Reservation.objects.exists())

    def test_9_1b_a_login_without_a_patient_record_cannot_reserve(self):
        self.assertFalse(reserve_medicine(self.omar, None, "Metformin 500 mg", 2)["reserved"])
        self.assertFalse(Reservation.objects.exists())


class PrescriptionStatusToolTests(PharmacyDataTestCase):
    def test_9_1b_prescriptions_come_newest_first_with_medicines_status_and_dates(self):
        now = timezone.now()
        collected = make_prescription(
            self.samira, self.metformin_500, "collected", ready_at=now, collected_at=now
        )
        ready = make_prescription(self.samira, self.metformin_850, "ready", ready_at=now)
        self.assertEqual(
            prescription_status(self.samira_user, self.samira),
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
        self.assertEqual(len(prescription_status(self.samira_user, self.samira)["prescriptions"]), 5)

    def test_9_1b_only_the_signed_in_patients_prescriptions_are_given(self):
        make_prescription(self.maya, self.metformin_500, "received")
        self.assertEqual(prescription_status(self.samira_user, self.samira), {"prescriptions": []})

    def test_9_1b_a_login_without_a_patient_record_has_no_prescriptions(self):
        self.assertEqual(prescription_status(self.omar, None), {"prescriptions": []})


class RequestRefillToolTests(PharmacyDataTestCase):
    def setUp(self):
        super().setUp()
        self.due_item = collected_long_term_item(self.samira, self.metformin_500, days_of_supply=2)
        self.not_due_item = collected_long_term_item(self.samira, self.metformin_850, days_of_supply=30)
        runs_out_on = timezone.localdate() + timedelta(days=2)
        self.due_refills = [{"medicine": "Metformin 500 mg", "runs_out_on": runs_out_on.strftime("%d %b %Y")}]

    def test_9_1b_a_due_refill_is_requested(self):
        result = request_refill(self.samira_user, self.samira, "Metformin 500 mg")
        self.assertEqual(
            result,
            {"requested": True, "medicine": "Metformin 500 mg", "status": "Waiting for the pharmacist"},
        )
        refill_request = RefillRequest.objects.get()
        self.assertEqual(refill_request.prescription_item, self.due_item)
        self.assertEqual(refill_request.requested_by, self.samira_user)

    def test_9_1b_without_a_medicine_the_due_refills_are_listed(self):
        result = request_refill(self.samira_user, self.samira)
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], self.due_refills)
        self.assertFalse(RefillRequest.objects.exists())

    def test_9_1b_a_refill_that_is_not_due_is_refused(self):
        result = request_refill(self.samira_user, self.samira, "Metformin 850 mg")
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], self.due_refills)
        self.assertFalse(RefillRequest.objects.exists())

    def test_9_1b_a_refill_is_not_requested_twice(self):
        request_refill(self.samira_user, self.samira, "Metformin 500 mg")
        result = request_refill(self.samira_user, self.samira, "Metformin 500 mg")
        self.assertFalse(result["requested"])
        self.assertEqual(result["due_refills"], [])
        self.assertEqual(RefillRequest.objects.count(), 1)

    def test_9_1b_a_login_without_a_patient_record_cannot_request_a_refill(self):
        self.assertFalse(request_refill(self.omar, None, "Metformin 500 mg")["requested"])
        self.assertFalse(RefillRequest.objects.exists())


class ToolPrivacyTests(PharmacyDataTestCase):
    def test_9_1b_no_tool_result_contains_the_patients_name_phone_birth_date_or_allergies(self):
        collected_long_term_item(self.samira, self.metformin_500, days_of_supply=2)
        make_prescription(self.samira, self.metformin_850, "ready", ready_at=timezone.now())
        results = [
            pharmacy_info(self.samira_user, self.samira),
            check_stock(self.samira_user, self.samira, "metformin"),
            reserve_medicine(self.samira_user, self.samira, "Metformin 850 mg", 1),
            prescription_status(self.samira_user, self.samira),
            request_refill(self.samira_user, self.samira),
            request_refill(self.samira_user, self.samira, "Metformin 500 mg"),
            ask_pharmacist(self.samira_user, self.samira, GRAPEFRUIT_QUESTION),
        ]
        sent_to_gemini = json.dumps(results)
        for private in ["Samira", "Haddad", "03 100 001", "1958", "Penicillins"]:
            self.assertNotIn(private, sent_to_gemini)


class SelectedPatientToolTests(PharmacyDataTestCase):
    def test_12_1_the_tools_act_for_the_patient_they_are_given(self):
        make_prescription(self.maya, self.metformin_500, "ready", ready_at=timezone.now())
        self.assertEqual(len(prescription_status(self.omar, self.maya)["prescriptions"]), 1)

    def test_12_1_a_caregiver_reserves_for_the_patient_they_care_for(self):
        self.assertTrue(reserve_medicine(self.omar, self.maya, "Metformin 500 mg", 1)["reserved"])
        self.assertEqual(Reservation.objects.get().patient, self.maya)

    def test_12_1_a_caregiver_requests_a_refill_for_the_patient_they_care_for(self):
        item = collected_long_term_item(self.maya, self.metformin_500, days_of_supply=2)
        self.assertTrue(request_refill(self.omar, self.maya, "Metformin 500 mg")["requested"])
        refill_request = RefillRequest.objects.get()
        self.assertEqual((refill_request.prescription_item, refill_request.requested_by), (item, self.omar))


class PharmacistQuestionTests(PharmacyDataTestCase):
    def test_9_3_a_question_is_saved_as_waiting_for_a_pharmacist(self):
        question = pass_to_pharmacist(self.samira_user, self.samira, GRAPEFRUIT_QUESTION)
        self.assertEqual(
            (question.patient, question.asked_by, question.question, question.status, question.answer),
            (self.samira, self.samira_user, GRAPEFRUIT_QUESTION, "waiting", ""),
        )
        self.assertIsNotNone(question.asked_at)
        self.assertIsNone(question.answered_at)

    def test_9_3_a_blank_question_is_refused(self):
        with self.assertRaises(ValidationError):
            pass_to_pharmacist(self.samira_user, self.samira, "   ")
        self.assertFalse(PharmacistQuestion.objects.exists())

    def test_9_3_questions_are_counted_for_the_user_who_asked(self):
        pass_to_pharmacist(self.samira_user, self.samira, GRAPEFRUIT_QUESTION)
        self.assertEqual(questions_asked_count(self.samira_user), 1)
        self.assertEqual(questions_asked_count(self.maya_user), 0)


class AskPharmacistToolTests(PharmacyDataTestCase):
    def test_9_3_a_medical_question_is_passed_to_a_pharmacist(self):
        result = ask_pharmacist(self.samira_user, self.samira, GRAPEFRUIT_QUESTION)
        self.assertEqual(
            result,
            {
                "passed_to_pharmacist": True,
                "reply_expected": f"During opening hours: {settings.PHARMACY_HOURS}",
                "reply_arrives": "As a notification in CarePharm.",
            },
        )
        question = PharmacistQuestion.objects.get()
        self.assertEqual((question.patient, question.question), (self.samira, GRAPEFRUIT_QUESTION))

    def test_9_3_a_caregiver_asks_for_the_patient_they_care_for(self):
        ask_pharmacist(self.omar, self.maya, GRAPEFRUIT_QUESTION)
        question = PharmacistQuestion.objects.get()
        self.assertEqual((question.patient, question.asked_by), (self.maya, self.omar))

    def test_9_3_without_a_patient_nothing_is_passed_on(self):
        self.assertEqual(
            ask_pharmacist(self.omar, None, GRAPEFRUIT_QUESTION),
            {"passed_to_pharmacist": False, "message": NO_PATIENT_RECORD},
        )
        self.assertFalse(PharmacistQuestion.objects.exists())

    def test_9_3_an_empty_question_is_not_passed_on(self):
        self.assertFalse(ask_pharmacist(self.samira_user, self.samira, "  ")["passed_to_pharmacist"])
        self.assertFalse(PharmacistQuestion.objects.exists())


class ToolDeclarationTests(SimpleTestCase):
    def test_9_1b_gemini_is_told_about_the_pharmacy_data_tools(self):
        self.assertEqual(
            [declaration["name"] for declaration in TOOL_DECLARATIONS],
            [
                "pharmacy_info",
                "check_stock",
                "reserve_medicine",
                "prescription_status",
                "request_refill",
                "ask_pharmacist",
            ],
        )

    def test_9_3_the_prompt_says_to_pass_medical_questions_to_a_pharmacist(self):
        prompt = system_prompt()
        self.assertIn("call ask_pharmacist", prompt)
        self.assertIn(settings.PHARMACY_HOURS, prompt)
        self.assertIn("notification", prompt)

    def test_9_1b_the_prompt_asks_to_confirm_before_reserving_or_requesting_a_refill(self):
        prompt = system_prompt().lower()
        self.assertIn("before reserving a medicine or requesting a refill", prompt)
        self.assertIn("ask the patient to confirm", prompt)

    def test_9_1b_the_prompt_asks_for_plain_text_because_the_chat_shows_no_markdown(self):
        self.assertIn("plain text only, without markdown", system_prompt().lower())


class CannotAnswerTests(SimpleTestCase):
    def setUp(self):
        self.user = get_user_model()(username="samira", role="patient")
        patcher = patch("assistant.services.run_agent")
        self.run_agent = patcher.start()
        self.addCleanup(patcher.stop)

    def test_9_1c_a_blank_answer_becomes_the_cannot_answer_message(self):
        self.run_agent.return_value = ("", "first")
        self.assertEqual(ask_assistant(self.user, None, "What is the weather?", None), (cannot_answer_message(), "first"))

    def test_9_1c_an_answer_of_only_spaces_counts_as_blank(self):
        self.run_agent.return_value = ("  \n ", "first")
        self.assertEqual(ask_assistant(self.user, None, "What is the weather?", None)[0], cannot_answer_message())

    def test_9_1c_no_answer_at_all_counts_as_blank(self):
        self.run_agent.return_value = (None, "first")
        self.assertEqual(ask_assistant(self.user, None, "What is the weather?", None)[0], cannot_answer_message())

    def test_9_1c_a_real_answer_is_kept(self):
        self.run_agent.return_value = ("We open at 8:00.", "first")
        self.assertEqual(ask_assistant(self.user, None, "When do you open?", None)[0], "We open at 8:00.")

    def test_9_1c_the_cannot_answer_message_says_so_and_gives_the_phone_and_hours(self):
        message = cannot_answer_message()
        self.assertTrue(message.startswith("Sorry, I can't answer that."))
        self.assertIn(settings.PHARMACY_PHONE, message)
        self.assertIn(settings.PHARMACY_HOURS, message)

    def test_9_1c_the_prompt_gives_one_opening_for_every_refusal(self):
        self.assertIn('start with "Sorry, I can\'t answer that."', system_prompt())

    def test_9_1c_the_prompt_gives_the_phone_number_for_refusals(self):
        self.assertIn(settings.PHARMACY_PHONE, system_prompt())


SHAKY_QUESTION = "Is it normal to feel shaky after using my inhaler?"
GRAPEFRUIT_ANSWER = "It is better to avoid grapefruit juice with Amlodipine. Orange or apple juice are fine."


class QuestionTestCase(PharmacyDataTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.rana = User.objects.create(username="rana", role="pharmacist")
        cls.karim = User.objects.create(username="karim", role="assistant")

    def ask(self, patient, question, minutes_ago):
        asked = pass_to_pharmacist(patient.user, patient, question)
        asked_at = timezone.now() - timedelta(minutes=minutes_ago)
        PharmacistQuestion.objects.filter(pk=asked.pk).update(asked_at=asked_at)
        asked.refresh_from_db()
        return asked

    def status_of(self, question):
        question.refresh_from_db()
        return question.status


class AnswerQuestionTests(QuestionTestCase):
    def test_9_4_waiting_questions_come_oldest_first(self):
        newer = self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        older = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        self.assertEqual(list(waiting_questions()), [older, newer])

    def test_9_4_answered_questions_come_newest_answer_first(self):
        first = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        second = self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        answer_question(first, GRAPEFRUIT_ANSWER)
        answer_question(second, "Yes, a little shakiness is common.")
        self.assertEqual(list(answered_questions()), [second, first])
        self.assertEqual(list(waiting_questions()), [])

    def test_9_4_waiting_and_answered_questions_are_counted(self):
        answer_question(self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60), GRAPEFRUIT_ANSWER)
        self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        self.assertEqual((waiting_question_count(), answered_question_count()), (1, 1))

    def test_9_4_answering_saves_the_answer_and_the_time(self):
        question = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        answer_question(question, f"  {GRAPEFRUIT_ANSWER}  ")
        question.refresh_from_db()
        self.assertEqual((question.status, question.answer), ("answered", GRAPEFRUIT_ANSWER))
        self.assertIsNotNone(question.answered_at)

    def test_9_4_answering_tells_the_patient(self):
        answer_question(self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60), GRAPEFRUIT_ANSWER)
        notification = Notification.objects.get(user=self.samira_user)
        self.assertEqual(notification.text, "A pharmacist answered Samira Haddad's question.")
        self.assertEqual(notification.link, "/assistant/questions/")

    def test_9_4_a_blank_answer_is_refused(self):
        question = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        with self.assertRaises(ValidationError):
            answer_question(question, "   ")
        self.assertEqual(self.status_of(question), "waiting")
        self.assertFalse(Notification.objects.exists())

    def test_9_4_a_question_is_answered_only_once(self):
        question = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        answer_question(question, GRAPEFRUIT_ANSWER)
        with self.assertRaises(ValidationError):
            answer_question(question, "A second answer.")
        question.refresh_from_db()
        self.assertEqual(question.answer, GRAPEFRUIT_ANSWER)
        self.assertEqual(Notification.objects.count(), 1)

    def test_9_4_a_patients_questions_come_newest_first(self):
        older = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        newer = self.ask(self.samira, SHAKY_QUESTION, minutes_ago=5)
        self.ask(self.maya, SHAKY_QUESTION, minutes_ago=1)
        self.assertEqual(list(questions_of(self.samira)), [newer, older])


class MessagesPageTests(QuestionTestCase):
    def setUp(self):
        super().setUp()
        self.grapefruit = self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        self.shaky = self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        self.client.force_login(self.rana)

    def reply(self, question, text):
        return self.client.post(f"/assistant/messages/{question.pk}/reply/", {"answer": text})

    def test_9_4_signed_out_visitors_go_to_the_staff_sign_in(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/assistant/messages/"), "/login/staff/?next=/assistant/messages/")

    def test_9_4_patients_cannot_open_the_messages(self):
        self.client.force_login(self.samira_user)
        self.assertEqual(self.client.get("/assistant/messages/").status_code, 403)

    def test_9_4_the_page_lists_the_waiting_questions_with_their_counts(self):
        response = self.client.get("/assistant/messages/")
        for text in ["Handovers", "Samira Haddad", GRAPEFRUIT_QUESTION, "Maya Khoury", SHAKY_QUESTION]:
            self.assertContains(response, text)
        self.assertContains(response, "Waiting 2")
        self.assertContains(response, "Answered 0")

    def test_9_4_the_longest_waiting_question_is_open_first(self):
        response = self.client.get("/assistant/messages/")
        self.assertContains(response, f'action="/assistant/messages/{self.grapefruit.pk}/reply/"')

    def test_9_4_a_chosen_question_opens(self):
        response = self.client.get(f"/assistant/messages/{self.shaky.pk}/")
        self.assertContains(response, f'action="/assistant/messages/{self.shaky.pk}/reply/"')

    def test_9_4_the_open_question_shows_the_patients_allergies_and_a_link_to_their_record(self):
        response = self.client.get("/assistant/messages/")
        self.assertContains(response, "Penicillins")
        self.assertContains(response, f'href="/patients/{self.samira.pk}/"')

    def test_9_4_a_pharmacists_reply_answers_the_question(self):
        response = self.reply(self.grapefruit, GRAPEFRUIT_ANSWER)
        self.assertRedirects(response, f"/assistant/messages/{self.grapefruit.pk}/")
        self.assertEqual(self.status_of(self.grapefruit), "answered")
        self.assertContains(self.client.get(response.url), "Reply sent to Samira Haddad.")

    def test_9_4_a_blank_reply_is_refused_with_a_message(self):
        response = self.reply(self.grapefruit, "   ")
        self.assertEqual(self.status_of(self.grapefruit), "waiting")
        self.assertContains(self.client.get(response.url), "Write a reply before sending.")

    def test_9_4_the_answered_pill_lists_the_answered_questions(self):
        answer_question(self.grapefruit, GRAPEFRUIT_ANSWER)
        response = self.client.get("/assistant/messages/?show=answered")
        self.assertContains(response, "Answered 1")
        self.assertContains(response, GRAPEFRUIT_ANSWER)
        self.assertNotContains(response, SHAKY_QUESTION)

    def test_9_4_an_answered_question_shows_its_answer_and_no_reply_box(self):
        answer_question(self.grapefruit, GRAPEFRUIT_ANSWER)
        response = self.client.get(f"/assistant/messages/{self.grapefruit.pk}/")
        self.assertContains(response, GRAPEFRUIT_ANSWER)
        self.assertNotContains(response, "Send Reply")

    def test_9_4_an_assistant_can_read_the_questions_but_not_reply(self):
        self.client.force_login(self.karim)
        response = self.client.get("/assistant/messages/")
        self.assertContains(response, GRAPEFRUIT_QUESTION)
        self.assertNotContains(response, "Send Reply")
        self.assertEqual(self.reply(self.grapefruit, GRAPEFRUIT_ANSWER).status_code, 403)
        self.assertEqual(self.status_of(self.grapefruit), "waiting")

    def test_9_4_with_no_waiting_questions_the_page_says_so(self):
        PharmacistQuestion.objects.all().delete()
        self.assertContains(self.client.get("/assistant/messages/"), "No questions waiting.")

    def test_9_4_the_console_menu_links_to_the_messages(self):
        self.assertContains(self.client.get("/dashboard/"), 'href="/assistant/messages/"')


class MyQuestionsPageTests(QuestionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.samira_user)

    def test_9_4_signed_out_visitors_go_to_the_patient_sign_in(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/assistant/questions/"), "/login/?next=/assistant/questions/")

    def test_9_4_staff_cannot_open_a_patients_questions(self):
        self.client.force_login(self.rana)
        self.assertEqual(self.client.get("/assistant/questions/").status_code, 403)

    def test_9_4_the_patient_reads_the_pharmacists_answer(self):
        answer_question(self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60), GRAPEFRUIT_ANSWER)
        response = self.client.get("/assistant/questions/")
        self.assertContains(response, GRAPEFRUIT_QUESTION)
        self.assertContains(response, "Answer from the pharmacist")
        self.assertContains(response, GRAPEFRUIT_ANSWER)

    def test_9_4_a_waiting_question_says_when_to_expect_a_reply(self):
        self.ask(self.samira, GRAPEFRUIT_QUESTION, minutes_ago=60)
        response = self.client.get("/assistant/questions/")
        self.assertContains(response, f"A pharmacist will reply during opening hours: {settings.PHARMACY_HOURS}")

    def test_9_4_only_the_selected_patients_questions_are_shown(self):
        self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        self.assertNotContains(self.client.get("/assistant/questions/"), SHAKY_QUESTION)

    def test_9_4_a_caregiver_sees_the_questions_of_the_patient_they_care_for(self):
        CaregiverLink.objects.create(patient=self.maya, caregiver=self.omar)
        self.ask(self.maya, SHAKY_QUESTION, minutes_ago=5)
        self.client.force_login(self.omar)
        self.assertContains(self.client.get("/assistant/questions/"), SHAKY_QUESTION)

    def test_9_4_with_no_questions_the_page_says_so(self):
        self.assertContains(self.client.get("/assistant/questions/"), "No questions yet.")

    def test_9_4_the_chats_handover_notice_links_to_my_questions(self):
        session = self.client.session
        session["assistant_history"] = [
            {"role": "patient", "text": GRAPEFRUIT_QUESTION},
            {"role": "assistant", "text": "I passed it to a pharmacist."},
            {"role": "handover", "text": "Passed to a pharmacist"},
        ]
        session.save()
        self.assertContains(self.client.get("/assistant/"), 'href="/assistant/questions/"')
