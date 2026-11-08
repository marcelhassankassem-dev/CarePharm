from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_CHOICES = [
        ("patient", "Patient"),
        ("pharmacist", "Pharmacist"),
        ("assistant", "Assistant"),
        ("owner", "Owner"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="patient")
    is_pharmacist = models.BooleanField(default=False)
