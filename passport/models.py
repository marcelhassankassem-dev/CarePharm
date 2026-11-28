from django.conf import settings
from django.db import models

from inventory.models import DrugClass, Medicine


class Patient(models.Model):
    name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    phone = models.CharField(max_length=20, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name


class Allergy(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    drug_class = models.ForeignKey(DrugClass, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.patient}: {self.drug_class}"


class CurrentMedicine(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.patient}: {self.medicine}"


class PassportShare(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    token = models.CharField(max_length=50, unique=True)
    pharmacy_name = models.CharField(max_length=100, blank=True)
    include_contact = models.BooleanField(default=False)
    include_allergies = models.BooleanField(default=False)
    include_medicines = models.BooleanField(default=False)
    include_history = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.patient} shared with {self.pharmacy_name or 'a pharmacy'}"
