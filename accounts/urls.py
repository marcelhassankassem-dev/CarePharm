from django.urls import path

from accounts import views

urlpatterns = [
    path("", views.site_root, name="site_root"),
    path("login/", views.patient_login, name="patient_login"),
    path("login/staff/", views.staff_login, name="staff_login"),
    path("logout/", views.sign_out, name="sign_out"),
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/add/", views.staff_add, name="staff_add"),
    path("staff/<int:member_id>/edit/", views.staff_edit, name="staff_edit"),
]
