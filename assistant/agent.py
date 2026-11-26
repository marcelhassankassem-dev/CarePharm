import json

from django.conf import settings
from google import genai

from assistant.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS

MODEL = "gemini-3.5-flash-lite"
THINKING_LEVEL = "minimal"
MAX_STEPS = 5
TIMEOUT_MILLISECONDS = 30000
STUCK_MESSAGE = "I got stuck working on that. Please try rephrasing."


def system_prompt():
    return f"""You are the online assistant of {settings.PHARMACY_NAME}, a small pharmacy.

Rules:
- Only answer questions about the pharmacy and its services: opening hours, location, medicine availability, reservations, prescriptions and refills.
- Always use a tool to answer questions about the pharmacy. Never guess.
- Never diagnose a condition and never recommend a medicine, a dose or a treatment.
- For any medical question, say that only a pharmacist can answer it, and that the pharmacy is open {settings.PHARMACY_HOURS}.
- If you cannot answer, or you are not sure, say clearly that you cannot answer.
- Keep your answers short and friendly.
"""


def gemini_client():
    return genai.Client(api_key=settings.GEMINI_API_KEY, http_options={"timeout": TIMEOUT_MILLISECONDS})


def ask_gemini(client, user_input, previous_interaction_id):
    return client.interactions.create(
        model=MODEL,
        input=user_input,
        tools=TOOL_DECLARATIONS,
        system_instruction=system_prompt(),
        previous_interaction_id=previous_interaction_id,
        generation_config={"thinking_level": THINKING_LEVEL},
    )


def run_tool(user, call):
    output = TOOL_FUNCTIONS[call.name](user, **call.arguments)
    return {
        "type": "function_result",
        "name": call.name,
        "call_id": call.id,
        "result": [{"type": "text", "text": json.dumps(output)}],
    }


def run_agent(user, message, previous_interaction_id=None):
    client = gemini_client()
    interaction = ask_gemini(client, message, previous_interaction_id)
    for _ in range(MAX_STEPS):
        calls = [step for step in interaction.steps if step.type == "function_call"]
        if not calls:
            return interaction.output_text, interaction.id
        results = [run_tool(user, call) for call in calls]
        interaction = ask_gemini(client, results, interaction.id)
    return STUCK_MESSAGE, interaction.id
