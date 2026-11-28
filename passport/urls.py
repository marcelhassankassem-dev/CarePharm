from django.urls import path

from passport import views

urlpatterns = [
    path("", views.patient_list, name="patient_list"),
    path("my/", views.my_passport, name="my_passport"),
    path("my/share/", views.passport_share, name="passport_share"),
    path("my/share/<int:share_id>/remove/", views.passport_share_remove, name="passport_share_remove"),
    path("new/", views.patient_new, name="patient_new"),
    path("logins/new/", views.patient_login_new, name="patient_login_new"),
    path("<int:patient_id>/", views.patient_list, name="patient_detail"),
]
