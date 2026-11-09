from django.urls import path

from accounts import views

urlpatterns = [
    path("", views.site_root, name="site_root"),
    path("login/", views.patient_login, name="patient_login"),
    path("login/staff/", views.staff_login, name="staff_login"),
]
