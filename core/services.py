from core.models import ActivityLog


def log_action(user, action, details=""):
    return ActivityLog.objects.create(user=user, action=action, details=details)
