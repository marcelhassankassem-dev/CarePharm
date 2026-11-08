from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.action
