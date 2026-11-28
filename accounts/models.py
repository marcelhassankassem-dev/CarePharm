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
    profile_photo = models.FileField(upload_to="profile_photos/users/", blank=True)


class CaregiverLink(models.Model):
    patient = models.ForeignKey("passport.Patient", on_delete=models.CASCADE)
    caregiver = models.ForeignKey(User, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)
