from django.db import models

from inventory.models import Batch, Medicine
from passport.models import Patient


class Prescription(models.Model):
    STATUS_CHOICES = [
        ("received", "Received"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("collected", "Collected"),
        ("returned", "Returned"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="received")
    created_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Prescription {self.pk} for {self.patient}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    days_of_supply = models.PositiveIntegerField()
    long_term = models.BooleanField(default=False)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True)

    def __str__(self):
        return f"{self.quantity} x {self.medicine}"
