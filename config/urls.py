from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django administration panel.
    path("admin/", admin.site.urls),

    # Send inventory requests to inventory/urls.py.
    path("inventory/", include("inventory.urls")),
]
