from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("passport", "0004_passportshare_include_contact")]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="profile_photo",
            field=models.FileField(blank=True, upload_to="profile_photos/patients/"),
        ),
    ]
