from django.conf import settings
from django.utils import timezone

from accounts.services import caregiver_links_of
from core.models import ActivityLog, Notification

LATEST_NOTIFICATIONS = 3


def pharmacy_details():
    return {
        "name": settings.PHARMACY_NAME,
        "address": settings.PHARMACY_ADDRESS,
        "phone": settings.PHARMACY_PHONE,
        "hours": settings.PHARMACY_HOURS,
    }


def log_action(user, action, details=""):
    return ActivityLog.objects.create(user=user, action=action, details=details)


def activity_log(today_only=False):
    entries = ActivityLog.objects.select_related("user").order_by("-time", "-id")
    if today_only:
        entries = entries.filter(time__date=timezone.localdate())
    return entries


def notify(user, text, link):
    return Notification.objects.create(user=user, text=text, link=link)


def notify_patient(patient, text, link):
    told = [patient.user] if patient.user else []
    told += [caregiver_link.caregiver for caregiver_link in caregiver_links_of(patient)]
    for user in told:
        notify(user, text, link)
    return told


def notifications_for(user):
    return Notification.objects.filter(user=user).order_by("read", "-id")


def latest_notifications(user):
    return notifications_for(user)[:LATEST_NOTIFICATIONS]


def unread_count(user):
    return Notification.objects.filter(user=user, read=False).count()


def mark_all_read(user):
    Notification.objects.filter(user=user, read=False).update(read=True)
