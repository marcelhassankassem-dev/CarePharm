from django.urls import path

from accounts import views

urlpatterns = [
    path("", views.site_root, name="site_root"),
    path("login/", views.patient_login, name="patient_login"),
    path("login/staff/", views.staff_login, name="staff_login"),
    path("logout/", views.sign_out, name="sign_out"),
    path("home/", views.patient_home, name="patient_home"),
    path("family/", views.family_access, name="family_access"),
    path("family/photo/me/", views.family_photo_me, name="family_photo_me"),
    path("family/photo/<int:patient_id>/", views.family_photo_patient, name="family_photo_patient"),
    path("family/add/", views.family_add, name="family_add"),
    path("family/<int:link_id>/remove/", views.family_remove, name="family_remove"),
    path("family/view/<int:patient_id>/", views.view_patient, name="view_patient"),
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/add/", views.staff_add, name="staff_add"),
    path("staff/<int:member_id>/edit/", views.staff_edit, name="staff_edit"),
]
