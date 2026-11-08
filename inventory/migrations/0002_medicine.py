import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Medicine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('strength', models.CharField(max_length=50)),
                ('barcode', models.CharField(max_length=50, unique=True)),
                ('price', models.DecimalField(decimal_places=2, max_digits=8)),
                ('minimum_quantity', models.PositiveIntegerField()),
                ('drug_class', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.drugclass')),
            ],
        ),
    ]
