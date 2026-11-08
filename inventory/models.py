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
