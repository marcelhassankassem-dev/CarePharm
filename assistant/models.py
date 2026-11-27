from django.conf import settings
from django.db import models

from passport.models import Patient


class PharmacistQuestion(models.Model):
    STATUS_CHOICES = [
        ("waiting", "Waiting"),
        ("answered", "Answered"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT)
    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    question = models.TextField()
    answer = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="waiting")
    asked_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Question from {self.patient}"
