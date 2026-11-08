from django.db import models


class DrugClass(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "drug classes"

    def __str__(self):
        return self.name
