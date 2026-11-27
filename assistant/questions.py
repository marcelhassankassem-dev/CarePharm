from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from assistant.models import PharmacistQuestion
from core.services import notify_patient

MY_QUESTIONS_LINK = "/assistant/questions/"


def pass_to_pharmacist(user, patient, question):
    if not question.strip():
        raise ValidationError("The question is empty.")
    return PharmacistQuestion.objects.create(patient=patient, asked_by=user, question=question.strip())


def questions_asked_count(user):
    return PharmacistQuestion.objects.filter(asked_by=user).count()


def waiting_questions():
    return PharmacistQuestion.objects.filter(status="waiting").select_related("patient").order_by("asked_at", "id")


def answered_questions():
    answered = PharmacistQuestion.objects.filter(status="answered").select_related("patient")
    return answered.order_by("-answered_at", "-id")


def waiting_question_count():
    return waiting_questions().count()


def answered_question_count():
    return answered_questions().count()


def get_question(question_id):
    return get_object_or_404(PharmacistQuestion.objects.select_related("patient"), pk=question_id)


def questions_of(patient):
    return PharmacistQuestion.objects.filter(patient=patient).order_by("-asked_at", "-id")


@transaction.atomic
def answer_question(question, answer):
    if question.status != "waiting":
        raise ValidationError("This question has already been answered.")
    if not answer.strip():
        raise ValidationError("Write a reply before sending.")
    question.answer = answer.strip()
    question.status = "answered"
    question.answered_at = timezone.now()
    question.save()
    notify_patient(question.patient, f"A pharmacist answered {question.patient.name}'s question.", MY_QUESTIONS_LINK)
    return question
