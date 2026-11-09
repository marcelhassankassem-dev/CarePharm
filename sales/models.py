from django.conf import settings
from django.db import models

from inventory.models import Medicine
from passport.models import Patient


class Sale(models.Model):
    PAYMENT_CHOICES = [
        ("cash", "Cash"),
        ("card", "Card"),
    ]
    SOURCE_CHOICES = [
        ("counter", "Counter"),
        ("prescription", "Prescription"),
        ("reservation", "Reservation"),
    ]

    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Sale {self.pk}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.medicine}"
