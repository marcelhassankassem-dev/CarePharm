import math
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import CaregiverLink
from core.services import notify, notify_caregivers, notify_patient
from inventory.services import available_stock, find_medicines, return_stock, take_stock
from prescriptions.models import PrescriptionItem
from prescriptions.services import UNCOLLECTED_STATUSES, collected_long_term_items, create_refill_prescription
from reservations.models import CaregiverRefillReminder, RefillRequest, Reservation
from sales.services import record_sale

HOLD_TIME = timedelta(hours=2)
SOON_MINUTES = 30
OPEN_REFILL_STATUSES = ["waiting", "delayed"]
FIND_LIMIT = 5
REMINDER_DAYS = 3
UNCOLLECTED_REFILL_AFTER = timedelta(days=2)


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
    requested = RefillRequest.objects.create(prescription_item=item, requested_by=user)
    if CaregiverLink.objects.filter(patient=item.prescription.patient, caregiver=user, active=True).exists():
        notify_caregivers(
            item.prescription.patient,
            f"Refill requested for {item.prescription.patient.name}: {item.medicine}.",
        )
    return requested


@transaction.atomic
def approve_refill(refill_request):
    if refill_request.status not in OPEN_REFILL_STATUSES:
        raise ValidationError("Only a new or delayed refill request can be approved.")
    prescription = create_refill_prescription(refill_request.prescription_item)
    refill_request.status = "approved"
    refill_request.new_prescription = prescription
    refill_request.save()
    notify_caregivers(
        prescription.patient,
        f"Refill approved for {prescription.patient.name}: {refill_request.prescription_item.medicine}.",
    )
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
    told = notify_patient(item.prescription.patient, text, "/")
    notify_caregivers(item.prescription.patient, text)
    return told


@transaction.atomic
def send_uncollected_refill_alerts(now=None, caregiver=None):
    """Notify current caregivers once, 48 hours after a refill becomes ready."""
    now = now or timezone.now()
    cutoff = now - UNCOLLECTED_REFILL_AFTER
    due = (
        RefillRequest.objects.select_for_update()
        .filter(
            status="approved",
            pickup_alert_sent_at__isnull=True,
            new_prescription__status="ready",
            new_prescription__ready_at__isnull=False,
            new_prescription__ready_at__lte=cutoff,
        )
        .select_related("prescription_item__medicine", "new_prescription__patient")
    )
    if caregiver is not None:
        authorized_patients = CaregiverLink.objects.filter(
            caregiver=caregiver, active=True
        ).values("patient_id")
        due = due.filter(new_prescription__patient_id__in=authorized_patients)
    alerted = 0
    for requested in due:
        patient = requested.new_prescription.patient
        recipients = notify_caregivers(
            patient,
            f"Refill not collected: {patient.name}'s {requested.prescription_item.medicine} "
            f"has been ready for pickup for 2 days. Please collect it from the pharmacy.",
        )
        if recipients:
            requested.pickup_alert_sent_at = now
            requested.save(update_fields=["pickup_alert_sent_at"])
            alerted += 1
    return alerted


@transaction.atomic
def send_caregiver_refill_reminders(today=None, caregiver=None):
    """Create one reminder per linked caregiver and medicine per day (0–3 days left)."""
    today = today or timezone.localdate()
    alerted = 0
    links = CaregiverLink.objects.filter(active=True).select_related("patient", "caregiver")
    if caregiver is not None:
        links = links.filter(caregiver=caregiver)
    for connection in links:
        for item in latest_item_of_each_medicine(collected_long_term_items(connection.patient)):
            days_left = (runs_out_on(item) - today).days
            if not 0 <= days_left <= REMINDER_DAYS or refill_in_progress(item):
                continue
            _, created = CaregiverRefillReminder.objects.get_or_create(
                prescription_item=item,
                caregiver=connection.caregiver,
                reminder_date=today,
            )
            if created:
                notify(
                    connection.caregiver,
                    f"Refill reminder for {connection.patient.name}: {runs_out_text(item.medicine, days_left)}",
                    "/family/",
                )
                alerted += 1
    return alerted


def caregiver_medicine_cards(patient):
    """Build medication summaries from real prescription/refill records."""
    if patient is None:
        return []
    items = (
        PrescriptionItem.objects.filter(prescription__patient=patient)
        .select_related("medicine__drug_class", "prescription")
        .order_by("-prescription__created_at", "-id")
    )
    latest = {}
    for item in items:
        latest.setdefault(item.medicine_id, item)

    refillable = {
        item.medicine_id: item for item in latest_item_of_each_medicine(collected_long_term_items(patient))
    }
    today = timezone.localdate()
    cards = []
    for item in latest.values():
        source = refillable.get(item.medicine_id)
        latest_request = (
            RefillRequest.objects.filter(prescription_item=source)
            .select_related("new_prescription")
            .order_by("-created_at", "-id")
            .first()
            if source else None
        )
        active_request = latest_request and (
            latest_request.status in OPEN_REFILL_STATUSES
            or latest_request.status == "approved"
            and latest_request.new_prescription
            and latest_request.new_prescription.status in UNCOLLECTED_STATUSES
        )
        if active_request and latest_request.status == "waiting":
            status, tone = "Refill requested", "pending"
        elif active_request and latest_request.status == "delayed":
            status, tone = f"Refill delayed to {latest_request.expected_on:%d %b %Y}" if latest_request.expected_on else "Refill delayed", "pending"
        elif active_request and latest_request.new_prescription.status == "ready":
            status, tone = "Refill ready for pickup", "urgent"
        elif active_request:
            status, tone = "Refill being prepared", "pending"
        elif item.prescription.status == "ready":
            status, tone = "Ready for pickup", "urgent"
        elif source:
            days_left = (runs_out_on(source) - today).days
            if days_left < 0:
                status, tone = f"Refill overdue by {-days_left} day(s)", "urgent"
            elif days_left == 0:
                status, tone = "Refill due today", "urgent"
            else:
                status, tone = f"Next refill in {days_left} days", "upcoming"
        else:
            status, tone = item.prescription.get_status_display(), "neutral"
        action_label = (
            "Track Refill" if active_request else
            "Pickup Details" if item.prescription.status == "ready" else "View Details"
        )
        if active_request and latest_request.new_prescription and latest_request.new_prescription.status == "ready":
            action_label = "Pickup Details"
        ready_prescription = (
            latest_request.new_prescription
            if active_request and latest_request.new_prescription
            else item.prescription
        )
        if active_request and latest_request.status == "waiting":
            status_note = "Your request is with the pharmacy"
        elif active_request and latest_request.status == "delayed":
            status_note = "The pharmacy will update you"
        elif ready_prescription.status == "ready":
            status_note = (
                f"Ready since {timezone.localtime(ready_prescription.ready_at):%d %b %Y}"
                if ready_prescription.ready_at else "Available at your pharmacy"
            )
        elif active_request:
            status_note = "The pharmacy is preparing it"
        elif source:
            status_note = f"Next refill: {runs_out_on(source):%d %b %Y}"
        else:
            status_note = f"{item.days_of_supply}-day prescription supply"
        cards.append({
            "medicine": item.medicine,
            "item": item,
            "status": status,
            "status_note": status_note,
            "tone": tone,
            "refill_item": source if source and not active_request else None,
            "can_view_pickup": item.prescription.status == "ready" or bool(active_request),
            "action_label": action_label,
            "target_prescription_id": ready_prescription.pk,
        })
    return cards


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
