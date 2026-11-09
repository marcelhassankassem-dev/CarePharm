from django.urls import path

from prescriptions import views

urlpatterns = [
    path("", views.prescription_list, name="prescription_list"),
    path("new/", views.prescription_new, name="prescription_new"),
    path("<int:prescription_id>/", views.prescription_detail, name="prescription_detail"),
    path("<int:prescription_id>/items/", views.prescription_add_item, name="prescription_add_item"),
    path("items/<int:item_id>/remove/", views.prescription_remove_item, name="prescription_remove_item"),
    path("<int:prescription_id>/delete/", views.prescription_delete, name="prescription_delete"),
    path("<int:prescription_id>/preparing/", views.prescription_preparing, name="prescription_preparing"),
    path("<int:prescription_id>/ready/", views.prescription_ready, name="prescription_ready"),
    path("<int:prescription_id>/collected/", views.prescription_collected, name="prescription_collected"),
    path("<int:prescription_id>/returned/", views.prescription_returned, name="prescription_returned"),
]
