from django.conf import settings

from assistant.agent import run_agent


def unavailable_message():
    return (
        "The assistant is not available right now. "
        f"Please try again later, or call us on {settings.PHARMACY_PHONE}."
    )


def ask_assistant(user, message, interaction_id):
    try:
        return run_agent(user, message, interaction_id)
    except Exception:
        return unavailable_message(), interaction_id
