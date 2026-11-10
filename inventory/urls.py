from django.urls import path

from inventory import views

urlpatterns = [
    path("", views.inventory_page, name="inventory_page"),
    path("delivery/", views.record_delivery_page, name="record_delivery_page"),
    path("<int:medicine_id>/adjust/", views.adjust_stock_page, name="adjust_stock_page"),
]
