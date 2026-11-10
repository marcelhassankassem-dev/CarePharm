from django.conf import settings
from django.db import models

from inventory.models import Medicine
from prescriptions.models import Prescription


class SafetyOverride(models.Model):
    WARNING_CHOICES = [
        ("allergy", "Allergy"),
        ("duplicate", "Duplicate"),
    ]

    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    warning_type = models.CharField(max_length=20, choices=WARNING_CHOICES)
    reason = models.TextField()
    pharmacist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_warning_type_display()} override for {self.medicine}"
