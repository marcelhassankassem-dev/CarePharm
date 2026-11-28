from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("reservations", "0003_refillrequest_expected_on"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="refillrequest",
            name="pickup_alert_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CaregiverRefillReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reminder_date", models.DateField()),
                ("caregiver", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ("prescription_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="prescriptions.prescriptionitem")),
            ],
        ),
        migrations.AddConstraint(
            model_name="caregiverrefillreminder",
            constraint=models.UniqueConstraint(
                fields=("prescription_item", "caregiver", "reminder_date"),
                name="unique_caregiver_refill_reminder_per_day",
            ),
        ),
    ]
