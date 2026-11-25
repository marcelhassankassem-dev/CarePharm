from django.db import models

from inventory.models import Batch, Medicine
from passport.models import Patient


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
