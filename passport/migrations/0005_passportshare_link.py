import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('passport', '0004_passportshare_include_contact'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PassportShare',
        ),
        migrations.CreateModel(
            name='PassportShare',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=50, unique=True)),
                ('pharmacy_name', models.CharField(blank=True, max_length=100)),
                ('include_contact', models.BooleanField(default=False)),
                ('include_allergies', models.BooleanField(default=False)),
                ('include_medicines', models.BooleanField(default=False)),
                ('include_history', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('revoked', models.BooleanField(default=False)),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='passport.patient')),
            ],
        ),
    ]
