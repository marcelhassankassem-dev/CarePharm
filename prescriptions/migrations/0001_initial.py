import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0003_batch'),
        ('passport', '0002_allergy_currentmedicine'),
    ]

    operations = [
        migrations.CreateModel(
            name='Prescription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('received', 'Received'), ('preparing', 'Preparing'), ('ready', 'Ready'), ('collected', 'Collected'), ('returned', 'Returned')], default='received', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ready_at', models.DateTimeField(blank=True, null=True)),
                ('collected_at', models.DateTimeField(blank=True, null=True)),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='passport.patient')),
            ],
        ),
        migrations.CreateModel(
            name='PrescriptionItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('days_of_supply', models.PositiveIntegerField()),
                ('long_term', models.BooleanField(default=False)),
                ('batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='inventory.batch')),
                ('medicine', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.medicine')),
                ('prescription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='prescriptions.prescription')),
            ],
        ),
    ]
