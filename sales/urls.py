from django.urls import path

from sales import views

urlpatterns = [
    path("", views.sale_page, name="sale_page"),
    path("add/<int:medicine_id>/", views.sale_add, name="sale_add"),
    path("remove/<int:medicine_id>/", views.sale_remove, name="sale_remove"),
    path("scan/", views.sale_scan, name="sale_scan"),
    path("patient/", views.sale_patient, name="sale_patient"),
    path("patient/remove/", views.sale_patient_remove, name="sale_patient_remove"),
    path("complete/", views.sale_complete, name="sale_complete"),
]
