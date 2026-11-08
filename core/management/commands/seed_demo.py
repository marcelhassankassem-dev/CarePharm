from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from passport.models import Patient

DEMO_PASSWORD = "carepharm123"


def get_or_create_user(username, role):
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return User.objects.get(username=username)
    return User.objects.create_user(username=username, password=DEMO_PASSWORD, role=role)


def create_staff():
    User = get_user_model()
    if not User.objects.filter(username="nadim").exists():
        User.objects.create_superuser(
            username="nadim",
            password=DEMO_PASSWORD,
            role="owner",
            is_pharmacist=True,
        )
    get_or_create_user("rana", "pharmacist")
    get_or_create_user("karim", "assistant")


def create_patient(name, date_of_birth, phone, user):
    Patient.objects.get_or_create(
        name=name,
        defaults={"date_of_birth": date_of_birth, "phone": phone, "user": user},
    )


def create_patients():
    samira = get_or_create_user("samira", "patient")
    maya = get_or_create_user("maya", "patient")
    omars_father = get_or_create_user("omars_father", "patient")
    get_or_create_user("omar", "patient")

    create_patient("Samira", date(1958, 3, 14), "03 100 001", samira)
    create_patient("Maya", date(1988, 9, 21), "03 100 002", maya)
    create_patient("Omar's Father", date(1949, 11, 5), "03 100 003", omars_father)
    create_patient("Ahmad", date(1980, 6, 2), "03 100 004", None)


class Command(BaseCommand):
    def handle(self, *args, **options):
        create_staff()
        create_patients()
