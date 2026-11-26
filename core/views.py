from django.shortcuts import render

from accounts.permissions import patient_required
from core.services import mark_all_read, notifications_for
from passport.views import patient_page_data


@patient_required
def notifications(request):
    shown = list(notifications_for(request.user))
    mark_all_read(request.user)
    context = patient_page_data(request)
    context["notifications"] = shown
    return render(request, "core/notifications.html", context)
