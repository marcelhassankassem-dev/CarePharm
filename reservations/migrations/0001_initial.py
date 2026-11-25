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
            name='Reservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('active', 'Active'), ('collected', 'Collected'), ('expired', 'Expired')], default='active', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.batch')),
                ('medicine', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.medicine')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='passport.patient')),
            ],
        ),
    ]
