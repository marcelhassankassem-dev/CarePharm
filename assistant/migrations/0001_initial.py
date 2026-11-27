import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('passport', '0002_allergy_currentmedicine'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PharmacistQuestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.TextField()),
                ('answer', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('waiting', 'Waiting'), ('answered', 'Answered')], default='waiting', max_length=20)),
                ('asked_at', models.DateTimeField(auto_now_add=True)),
                ('answered_at', models.DateTimeField(blank=True, null=True)),
                ('asked_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='passport.patient')),
            ],
        ),
    ]
