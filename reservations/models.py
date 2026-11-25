from django.conf import settings
from django.db import models

from inventory.models import Batch, Medicine
from passport.models import Patient
from prescriptions.models import Prescription, PrescriptionItem


class Reservation(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("collected", "Collected"),
        ("expired", "Expired"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.quantity} x {self.medicine} for {self.patient}"


class RefillRequest(models.Model):
    STATUS_CHOICES = [
        ("waiting", "New"),
        ("approved", "Approved"),
        ("delayed", "Delayed"),
    ]

    prescription_item = models.ForeignKey(PrescriptionItem, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="waiting")
    created_at = models.DateTimeField(auto_now_add=True)
    new_prescription = models.ForeignKey(Prescription, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Refill of {self.prescription_item}"
