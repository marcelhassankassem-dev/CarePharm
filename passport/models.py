from django.conf import settings
from django.db import models

from inventory.models import DrugClass, Medicine


class Patient(models.Model):
    name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    phone = models.CharField(max_length=20, blank=True)
    profile_photo = models.FileField(upload_to="profile_photos/patients/", blank=True)
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
    """Patient consent to share selected passport sections with CarePharm staff."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="passport_shares")
    consented_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="passport_consents"
    )
    pharmacy_name = models.CharField(max_length=100)
    include_contact = models.BooleanField(default=False)
    include_allergies = models.BooleanField(default=False)
    include_medicines = models.BooleanField(default=False)
    include_history = models.BooleanField(default=False)
    consented_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.patient} shared with {self.pharmacy_name}"
