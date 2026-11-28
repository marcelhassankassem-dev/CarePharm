from django.contrib import admin
from django.urls import include, path

from passport.views import shared_passport_page

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("patients/", include("passport.urls")),
    path("passport/<str:token>/", shared_passport_page, name="shared_passport"),
    path("inventory/", include("inventory.urls")),
    path("prescriptions/", include("prescriptions.urls")),
    path("sales/", include("sales.urls")),
    path("reservations/", include("reservations.urls")),
    path("assistant/", include("assistant.urls")),
    path("", include("reports.urls")),
    path("", include("core.urls")),
]
