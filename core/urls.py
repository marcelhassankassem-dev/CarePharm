from django.urls import path

from core import views

urlpatterns = [
    path("notifications/", views.notifications, name="notifications"),
]
