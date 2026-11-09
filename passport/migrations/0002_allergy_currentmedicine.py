import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_batch'),
        ('passport', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Allergy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('drug_class', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.drugclass')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='passport.patient')),
            ],
        ),
        migrations.CreateModel(
            name='CurrentMedicine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('medicine', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.medicine')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='passport.patient')),
            ],
        ),
    ]
