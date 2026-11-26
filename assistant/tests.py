import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from assistant.agent import MAX_STEPS, MODEL, gemini_client, run_agent, system_prompt
from assistant.services import ask_assistant, unavailable_message
from assistant.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS, pharmacy_info
from assistant.views import QUICK_OPTIONS
from core.services import pharmacy_details
from passport.models import Patient

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
