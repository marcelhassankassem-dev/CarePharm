from django.db import models


class DrugClass(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Medicine(models.Model):
    name = models.CharField(max_length=100)
    strength = models.CharField(max_length=50)
    barcode = models.CharField(max_length=50, unique=True)
    drug_class = models.ForeignKey(DrugClass, on_delete=models.PROTECT)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    minimum_quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.name} {self.strength}"


class Batch(models.Model):
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    batch_number = models.CharField(max_length=50)
    expiry_date = models.DateField()
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=8, decimal_places=2)
    supplier = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)

    # Unique identifier for a delivery submission.
    # Prevents the same request from creating duplicate deliveries.
    # NULL is allowed for existing batches and demonstration data.
    delivery_request_id = models.UUIDField(
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )


    def __str__(self):
        return f"{self.medicine} ({self.batch_number})"
