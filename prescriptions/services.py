from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from inventory.services import return_stock, take_stock
from passport.services import add_current_medicine
from prescriptions.models import Prescription, PrescriptionItem
from sales.services import record_sale

ITEMS_ARE_LOCKED = "Medicines can only be changed while the prescription is Received."
UNCOLLECTED_STATUSES = ["received", "preparing", "ready"]


def check_status(prescription, status, problem):
    if prescription.status != status:
        raise ValidationError(problem)


def create_prescription(patient):
    return Prescription.objects.create(patient=patient)


def get_prescription(prescription_id):
    return get_object_or_404(Prescription.objects.select_related("patient"), pk=prescription_id)


def get_item(item_id):
    return get_object_or_404(PrescriptionItem.objects.select_related("prescription"), pk=item_id)


def items_of(prescription):
    return prescription.prescriptionitem_set.select_related("medicine", "batch").order_by("id")


def uncollected_prescriptions():
    return Prescription.objects.filter(status__in=UNCOLLECTED_STATUSES)


def uncollected_count():
    return uncollected_prescriptions().count()


def patients_matching(prescriptions, search):
    if search:
        prescriptions = prescriptions.filter(Q(patient__name__icontains=search) | Q(patient__phone__icontains=search))
    return prescriptions


def uncollected_rows(search=""):
    prescriptions = uncollected_prescriptions().select_related("patient").order_by("-created_at", "-id")
    prescriptions = patients_matching(prescriptions, search)
    return [{"prescription": prescription, "items": list(items_of(prescription))} for prescription in prescriptions]


def ready_prescriptions():
    return Prescription.objects.filter(status="ready")


def ready_count():
    return ready_prescriptions().count()


def days_waiting(prescription):
    return (timezone.localdate() - timezone.localdate(prescription.ready_at)).days


def ready_rows(search=""):
    prescriptions = ready_prescriptions().select_related("patient").order_by("ready_at", "id")
    prescriptions = patients_matching(prescriptions, search)
    return [
        {
            "prescription": prescription,
            "items": list(items_of(prescription)),
            "days_waiting": days_waiting(prescription),
        }
        for prescription in prescriptions
    ]


def add_item(prescription, medicine, quantity, days_of_supply, long_term=False):
    check_status(prescription, "received", ITEMS_ARE_LOCKED)
    return PrescriptionItem.objects.create(
        prescription=prescription,
        medicine=medicine,
        quantity=quantity,
        days_of_supply=days_of_supply,
        long_term=long_term,
    )


def remove_item(item):
    check_status(item.prescription, "received", ITEMS_ARE_LOCKED)
    item.delete()


def delete_prescription(prescription):
    check_status(prescription, "received", "Only a Received prescription can be deleted.")
    prescription.delete()


def start_preparing(prescription):
    check_status(prescription, "received", "Only a Received prescription can start preparing.")
    if not prescription.prescriptionitem_set.exists():
        raise ValidationError("Add at least one medicine first.")
    prescription.status = "preparing"
    prescription.save()


@transaction.atomic
def mark_ready(user, prescription):
    check_status(prescription, "preparing", "Only a Preparing prescription can be marked Ready.")
    for item in items_of(prescription):
        item.batch = take_stock(user, item.medicine, item.quantity)
        item.save()
    prescription.status = "ready"
    prescription.ready_at = timezone.now()
    prescription.save()


@transaction.atomic
def mark_collected(user, prescription, payment_method):
    check_status(prescription, "ready", "Only a Ready prescription can be marked Collected.")
    items = list(items_of(prescription))
    sold = [(item.medicine, item.quantity) for item in items]
    record_sale(user, payment_method, sold, "prescription", prescription.patient)
    for item in items:
        if item.long_term:
            add_current_medicine(prescription.patient, item.medicine)
    prescription.status = "collected"
    prescription.collected_at = timezone.now()
    prescription.save()


@transaction.atomic
def mark_returned(user, prescription):
    check_status(prescription, "ready", "Only a Ready prescription can be returned to stock.")
    for item in items_of(prescription):
        return_stock(user, item.batch, item.quantity)
    prescription.status = "returned"
    prescription.save()
