from django.shortcuts import redirect, render

from accounts.permissions import patient_required
from assistant.services import ask_assistant
from passport.services import patient_of

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


def send_message(session, user, message):
    answer, interaction_id = ask_assistant(user, patient_of(user), message, session.get(INTERACTION_ID))
    session[HISTORY] = session.get(HISTORY, []) + [
        {"role": "patient", "text": message},
        {"role": "assistant", "text": answer},
    ]
    session[INTERACTION_ID] = interaction_id


@patient_required
def chat(request):
    if request.method == "POST":
        message = request.POST.get("message", "").strip()
        if message:
            send_message(request.session, request.user, message)
        return redirect("assistant_chat")
    context = {"history": request.session.get(HISTORY, []), "quick_options": QUICK_OPTIONS}
    return render(request, "assistant/chat.html", context)


@patient_required
def start_over(request):
    request.session.pop(HISTORY, None)
    request.session.pop(INTERACTION_ID, None)
    return redirect("assistant_chat")
