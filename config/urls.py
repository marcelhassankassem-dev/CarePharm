from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("patients/", include("passport.urls")),
    path("inventory/", include("inventory.urls")),
    path("prescriptions/", include("prescriptions.urls")),
    path("sales/", include("sales.urls")),
    path("reservations/", include("reservations.urls")),
    path("assistant/", include("assistant.urls")),
    path("", include("reports.urls")),
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
