import math
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from inventory.services import return_stock, take_stock
from reservations.models import Reservation
from sales.services import record_sale

HOLD_TIME = timedelta(hours=2)
SOON_MINUTES = 30


@transaction.atomic
def reserve_medicine(user, patient, medicine, quantity):
    batch = take_stock(user, medicine, quantity)
    return Reservation.objects.create(patient=patient, medicine=medicine, quantity=quantity, batch=batch)


@transaction.atomic
def expire_reservations():
    cutoff = timezone.now() - HOLD_TIME
    for reservation in Reservation.objects.filter(status="active", created_at__lte=cutoff).select_related("batch"):
        switched = Reservation.objects.filter(pk=reservation.pk, status="active").update(status="expired")
        if switched:
            return_stock(None, reservation.batch, reservation.quantity)


@transaction.atomic
def collect_reservation(staff, reservation, payment_method):
    if reservation.status != "active":
        raise ValidationError("Only an active reservation can be collected.")
    items = [(reservation.medicine, reservation.quantity)]
    sale = record_sale(staff, payment_method, items, "reservation", reservation.patient)
    reservation.status = "collected"
    reservation.save()
    return sale


def get_reservation(reservation_id):
    return get_object_or_404(Reservation.objects.select_related("patient", "medicine"), pk=reservation_id)


def minutes_left(reservation, now):
    seconds = (reservation.created_at + HOLD_TIME - now).total_seconds()
    return max(0, math.ceil(seconds / 60))


def time_left_text(minutes):
    hours = minutes // 60
    if hours:
        return f"{hours} h {minutes % 60:02d} min left"
    return f"{minutes} min left"


def active_reservation_count():
    return Reservation.objects.filter(status="active").count()


def reservation_row(reservation, now):
    if reservation.status != "active":
        return {"reservation": reservation, "time_left": None, "soon": False}
    minutes = minutes_left(reservation, now)
    return {"reservation": reservation, "time_left": time_left_text(minutes), "soon": minutes < SOON_MINUTES}


def reservation_rows(search=""):
    now = timezone.now()
    reservations = Reservation.objects.select_related("patient", "medicine")
    if search:
        reservations = reservations.filter(Q(patient__name__icontains=search) | Q(patient__phone__icontains=search))
    active = reservations.filter(status="active").order_by("created_at", "id")
    ended = reservations.exclude(status="active").filter(created_at__gte=now - timedelta(days=1))
    ordered = list(active) + list(ended.order_by("-created_at", "-id"))
    return [reservation_row(reservation, now) for reservation in ordered]
