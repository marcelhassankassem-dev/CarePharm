from django.utils import timezone

from core.models import ActivityLog, Notification


def log_action(user, action, details=""):
    return ActivityLog.objects.create(user=user, action=action, details=details)


def activity_log(today_only=False):
    entries = ActivityLog.objects.select_related("user").order_by("-time", "-id")
    if today_only:
        entries = entries.filter(time__date=timezone.localdate())
    return entries


def notify(user, text, link):
    return Notification.objects.create(user=user, text=text, link=link)


def notifications_for(user):
    return Notification.objects.filter(user=user).order_by("read", "-id")
