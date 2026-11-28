from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_caregiverlink")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_photo",
            field=models.FileField(blank=True, upload_to="profile_photos/users/"),
        ),
    ]
