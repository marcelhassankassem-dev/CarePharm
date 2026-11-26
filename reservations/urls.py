from django.urls import path

from reservations import views

urlpatterns = [
    path("<int:reservation_id>/collect/", views.reservation_collect, name="reservation_collect"),
    path("refills/<int:refill_request_id>/approve/", views.refill_approve, name="refill_approve"),
    path("refills/<int:refill_request_id>/delay/", views.refill_delay, name="refill_delay"),
]
