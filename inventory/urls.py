from django.urls import path

from inventory import views

urlpatterns = [
    path("", views.inventory_page, name="inventory_page"),
    path("delivery/", views.record_delivery_page, name="record_delivery_page"),
]
