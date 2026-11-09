from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("patients/", include("passport.urls")),
    path("inventory/", include("inventory.urls")),
]
