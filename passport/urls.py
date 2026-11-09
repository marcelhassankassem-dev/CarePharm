from django.urls import path

from passport import views

urlpatterns = [
    path("", views.patient_list, name="patient_list"),
    path("new/", views.patient_new, name="patient_new"),
    path("logins/new/", views.patient_login_new, name="patient_login_new"),
    path("<int:patient_id>/", views.patient_list, name="patient_detail"),
]
