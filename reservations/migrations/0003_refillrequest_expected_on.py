from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0002_refillrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='refillrequest',
            name='expected_on',
            field=models.DateField(blank=True, null=True),
        ),
    ]
