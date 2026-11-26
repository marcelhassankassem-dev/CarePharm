from django.urls import path

from reservations import views

urlpatterns = [
    path("<int:reservation_id>/collect/", views.reservation_collect, name="reservation_collect"),
    path("refills/<int:refill_request_id>/approve/", views.refill_approve, name="refill_approve"),
    path("refills/<int:refill_request_id>/delay/", views.refill_delay, name="refill_delay"),
    path("find/", views.find_medicine, name="find_medicine"),
    path("find/results/", views.find_medicine_results, name="find_medicine_results"),
    path("reserve/", views.reserve, name="reserve"),
    path("<int:reservation_id>/", views.reservation_page, name="reservation_page"),
]
