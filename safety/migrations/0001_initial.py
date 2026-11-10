import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0003_batch'),
        ('prescriptions', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SafetyOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('warning_type', models.CharField(choices=[('allergy', 'Allergy'), ('duplicate', 'Duplicate')], max_length=20)),
                ('reason', models.TextField()),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('medicine', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.medicine')),
                ('pharmacist', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('prescription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='prescriptions.prescription')),
            ],
        ),
    ]
