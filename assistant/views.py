from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import can_act_as_pharmacist, patient_required, pharmacist_required, staff_required
from assistant.questions import (
    answer_question,
    answered_question_count,
    answered_questions,
    get_question,
    questions_asked_count,
    questions_of,
    waiting_question_count,
    waiting_questions,
)
from assistant.services import ask_assistant
from passport.services import allergies_of, current_medicines_of
from passport.views import patient_page_data, selected_patient

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


@patient_required
def my_questions(request):
    context = patient_page_data(request)
    context["questions"] = questions_of(selected_patient(request))
    context["pharmacy_hours"] = settings.PHARMACY_HOURS
    return render(request, "assistant/questions.html", context)


def open_question_data(question):
    if question is None:
        return {}
    return {"allergies": allergies_of(question.patient), "current_medicines": current_medicines_of(question.patient)}


@staff_required
def messages_page(request, question_id=None):
    question = get_question(question_id) if question_id else None
    show = request.GET.get("show") or (question.status if question else "waiting")
    questions = answered_questions() if show == "answered" else waiting_questions()
    if question is None:
        question = questions.first()
    context = {
        "show": show,
        "questions": questions,
        "question": question,
        "waiting_count": waiting_question_count(),
        "answered_count": answered_question_count(),
        "can_reply": can_act_as_pharmacist(request.user),
        **open_question_data(question),
    }
    return render(request, "assistant/messages.html", context)


@pharmacist_required
def message_reply(request, question_id):
    question = get_question(question_id)
    if request.method == "POST":
        try:
            answer_question(question, request.POST.get("answer", ""))
        except ValidationError as problem:
            messages.error(request, " ".join(problem.messages))
        else:
            messages.success(request, f"Reply sent to {question.patient.name}.")
    return redirect("message_question", question.pk)
