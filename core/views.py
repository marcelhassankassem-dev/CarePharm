from django.shortcuts import render

from accounts.permissions import patient_required
from core.services import mark_all_read, notifications_for
from passport.views import patient_page_data


@patient_required
def notifications(request):
    from reservations.services import send_caregiver_refill_reminders, send_uncollected_refill_alerts

    send_caregiver_refill_reminders(caregiver=request.user)
    send_uncollected_refill_alerts(caregiver=request.user)
    shown = list(notifications_for(request.user))
    mark_all_read(request.user)
    context = patient_page_data(request)
    context["notifications"] = shown
    return render(request, "core/notifications.html", context)
