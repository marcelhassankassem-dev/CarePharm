import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0003_batch'),
        ('passport', '0002_allergy_currentmedicine'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Sale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_method', models.CharField(choices=[('cash', 'Cash'), ('card', 'Card')], max_length=10)),
                ('source', models.CharField(choices=[('counter', 'Counter'), ('prescription', 'Prescription'), ('reservation', 'Reservation')], max_length=20)),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('patient', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='passport.patient')),
                ('staff', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='SaleItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=8)),
                ('medicine', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.medicine')),
                ('sale', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='sales.sale')),
            ],
        ),
    ]
