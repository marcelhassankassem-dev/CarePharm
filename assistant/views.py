from django.conf import settings
from django.shortcuts import redirect, render

from accounts.permissions import patient_required
from assistant.questions import questions_asked_count
from assistant.services import ask_assistant
from passport.views import selected_patient

HISTORY = "assistant_history"
INTERACTION_ID = "assistant_interaction_id"

QUICK_OPTIONS = [
    {"label": "Find a medicine", "question": "I want to find a medicine.", "icon": "chat-find-medicine.png"},
    {
        "label": "Check prescription status",
        "question": "What is the status of my prescription?",
        "icon": "chat-prescription.png",
    },
    {"label": "Request a refill", "question": "I want to request a refill.", "icon": "chat-refill.png"},
    {
        "label": "Pharmacy opening hours",
        "question": "What are your opening hours and where are you?",
        "icon": "chat-hours.png",
    },
    {"label": "General questions", "question": "What can you help me with?", "icon": "chat-general.png"},
]


def handover_notice():
    return f"Passed to a pharmacist · Reply expected during opening hours: {settings.PHARMACY_HOURS}"


def send_message(request, message):
    asked_before = questions_asked_count(request.user)
    answer, interaction_id = ask_assistant(
        request.user, selected_patient(request), message, request.session.get(INTERACTION_ID)
    )
    history = request.session.get(HISTORY, []) + [
        {"role": "patient", "text": message},
        {"role": "assistant", "text": answer},
    ]
    if questions_asked_count(request.user) > asked_before:
        history.append({"role": "handover", "text": handover_notice()})
    request.session[HISTORY] = history
    request.session[INTERACTION_ID] = interaction_id


@patient_required
def chat(request):
    if request.method == "POST":
        message = request.POST.get("message", "").strip()
        if message:
            send_message(request, message)
        return redirect("assistant_chat")
    context = {"history": request.session.get(HISTORY, []), "quick_options": QUICK_OPTIONS}
    return render(request, "assistant/chat.html", context)


@patient_required
def start_over(request):
    request.session.pop(HISTORY, None)
    request.session.pop(INTERACTION_ID, None)
    return redirect("assistant_chat")
