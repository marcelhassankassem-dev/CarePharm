from django.conf import settings

from assistant.agent import run_agent


def unavailable_message():
    return (
        "The assistant is not available right now. "
        f"Please try again later, or call us on {settings.PHARMACY_PHONE}."
    )


def cannot_answer_message():
    return f"Sorry, I can't answer that. Please call us on {settings.PHARMACY_PHONE}, {settings.PHARMACY_HOURS}."


def ask_assistant(user, patient, message, interaction_id):
    try:
        answer, interaction_id = run_agent(user, patient, message, interaction_id)
    except Exception:
        return unavailable_message(), interaction_id
    if not answer or not answer.strip():
        return cannot_answer_message(), interaction_id
    return answer, interaction_id
