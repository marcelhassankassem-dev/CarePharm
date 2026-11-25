import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prescriptions', '0002_prescription_refill_of'),
        ('reservations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RefillRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('waiting', 'New'), ('approved', 'Approved'), ('delayed', 'Delayed')], default='waiting', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('new_prescription', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='prescriptions.prescription')),
                ('prescription_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='prescriptions.prescriptionitem')),
                ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
