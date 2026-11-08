from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        PATIENT = "patient", "Patient"
        PHARMACIST = "pharmacist", "Pharmacist"
        ASSISTANT = "assistant", "Assistant"
        OWNER = "owner", "Owner"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PATIENT)
    is_pharmacist = models.BooleanField(default=False)
