import math
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.services import notify_patient
from inventory.services import available_stock, find_medicines, return_stock, take_stock
from prescriptions.services import UNCOLLECTED_STATUSES, collected_long_term_items, create_refill_prescription
from reservations.models import RefillRequest, Reservation
from sales.services import record_sale

HOLD_TIME = timedelta(hours=2)
SOON_MINUTES = 30
OPEN_REFILL_STATUSES = ["waiting", "delayed"]
FIND_LIMIT = 5
REMINDER_DAYS = 3


def availability(medicine, quantity):
    available = available_stock(medicine)
    if available >= quantity:
        status, label = "available", "Available"
    elif available > 0:
        status, label = "low", f"Only {available} left"
    else:
        status, label = "out", "Out of stock"
    return {"medicine": medicine, "available": available, "status": status, "label": label}


def medicine_availability(name, quantity):
    if not name:
        return []
    return [availability(medicine, quantity) for medicine in find_medicines(name)[:FIND_LIMIT]]


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


def pickup_deadline(reservation):
    return reservation.created_at + HOLD_TIME


def patient_reservation(patient, reservation_id):
    return get_object_or_404(Reservation.objects.select_related("medicine"), pk=reservation_id, patient=patient)


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


def open_refill_requests():
    return RefillRequest.objects.filter(status__in=OPEN_REFILL_STATUSES)


@transaction.atomic
def request_refill(user, item):
    if open_refill_requests().filter(prescription_item=item).exists():
        raise ValidationError(f"A refill of {item.medicine} has already been requested.")
    return RefillRequest.objects.create(prescription_item=item, requested_by=user)


@transaction.atomic
def approve_refill(refill_request):
    if refill_request.status not in OPEN_REFILL_STATUSES:
        raise ValidationError("Only a new or delayed refill request can be approved.")
    prescription = create_refill_prescription(refill_request.prescription_item)
    refill_request.status = "approved"
    refill_request.new_prescription = prescription
    refill_request.save()
    return prescription


@transaction.atomic
def delay_refill(refill_request, expected_on):
    if refill_request.status != "waiting":
        raise ValidationError("Only a new refill request can be delayed.")
    if expected_on <= timezone.localdate():
        raise ValidationError("The expected date must be after today.")
    refill_request.status = "delayed"
    refill_request.expected_on = expected_on
    refill_request.save()
    item = refill_request.prescription_item
    text = f"Refill delayed: {item.medicine}, expected {expected_on:%d %b %Y}."
    return notify_patient(item.prescription.patient, text, "/")


def get_refill_request(refill_request_id):
    return get_object_or_404(RefillRequest, pk=refill_request_id)


def open_refill_count():
    return open_refill_requests().count()


def new_refill_count():
    return RefillRequest.objects.filter(status="waiting").count()


def runs_out_on(item):
    collected_on = timezone.localtime(item.prescription.collected_at).date()
    return collected_on + timedelta(days=item.days_of_supply)


def runs_out_text(medicine, days_left):
    if days_left > 1:
        return f"Your {medicine} will run out in {days_left} days."
    if days_left == 1:
        return f"Your {medicine} will run out tomorrow."
    if days_left == 0:
        return f"Your {medicine} runs out today."
    if days_left == -1:
        return f"Your {medicine} ran out yesterday."
    return f"Your {medicine} ran out {-days_left} days ago."


def refill_in_progress(item):
    open_request = Q(status__in=OPEN_REFILL_STATUSES)
    approved_and_on_its_way = Q(status="approved", new_prescription__status__in=UNCOLLECTED_STATUSES)
    return RefillRequest.objects.filter(open_request | approved_and_on_its_way, prescription_item=item).exists()


def latest_item_of_each_medicine(items):
    latest = {}
    for item in items:
        latest.setdefault(item.medicine_id, item)
    return list(latest.values())


def refill_reminder(item, today):
    days_left = (runs_out_on(item) - today).days
    return {
        "item": item,
        "runs_out_on": runs_out_on(item),
        "days_left": days_left,
        "text": runs_out_text(item.medicine, days_left),
    }


def refill_reminders(patient):
    today = timezone.localdate()
    reminders = []
    for item in latest_item_of_each_medicine(collected_long_term_items(patient)):
        reminder = refill_reminder(item, today)
        if reminder["days_left"] <= REMINDER_DAYS and not refill_in_progress(item):
            reminders.append(reminder)
    return sorted(reminders, key=lambda reminder: reminder["days_left"])


def next_refill_dates(patient):
    items = latest_item_of_each_medicine(collected_long_term_items(patient))
    return {item.medicine_id: runs_out_on(item) for item in items}


def patient_refill_requests(patient):
    return (
        open_refill_requests()
        .filter(prescription_item__prescription__patient=patient)
        .select_related("prescription_item__medicine")
        .order_by("created_at", "id")
    )


def refill_rows(search=""):
    requests = open_refill_requests().select_related(
        "prescription_item__medicine",
        "prescription_item__prescription__patient",
    )
    if search:
        requests = requests.filter(
            Q(prescription_item__prescription__patient__name__icontains=search)
            | Q(prescription_item__prescription__patient__phone__icontains=search)
        )
    return [
        {"request": request, "out_of_stock": available_stock(request.prescription_item.medicine) == 0}
        for request in requests.order_by("created_at", "id")
    ]
