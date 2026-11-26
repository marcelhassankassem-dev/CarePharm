import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings

from assistant.agent import MAX_STEPS, MODEL, gemini_client, run_agent, system_prompt
from assistant.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS, pharmacy_info
from core.services import pharmacy_details

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
        client_class.assert_called_once_with(api_key="test-key")
