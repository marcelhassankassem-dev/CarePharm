from django.core.exceptions import ValidationError

from assistant.models import PharmacistQuestion


def pass_to_pharmacist(user, patient, question):
    if not question.strip():
        raise ValidationError("The question is empty.")
    return PharmacistQuestion.objects.create(patient=patient, asked_by=user, question=question.strip())


def questions_asked_count(user):
    return PharmacistQuestion.objects.filter(asked_by=user).count()
