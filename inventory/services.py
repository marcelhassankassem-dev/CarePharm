from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.services import log_action
from inventory.models import Batch, Medicine

NEAR_EXPIRY_DAYS = 90


def available_stock(medicine):
    batches = medicine.batch_set.filter(expiry_date__gte=timezone.localdate())
    return batches.aggregate(Sum("quantity"))["quantity__sum"] or 0


@transaction.atomic
def record_delivery(user, medicine, supplier, batch_number, expiry_date, quantity, unit_cost):
    batch = Batch.objects.create(
        medicine=medicine,
        supplier=supplier,
        batch_number=batch_number,
        expiry_date=expiry_date,
        quantity=quantity,
        unit_cost=unit_cost,
    )
    log_action(user, "Recorded delivery", f"{medicine} · batch {batch_number} · +{quantity}")
    return batch


def check_quantity(quantity):
    if quantity < 1:
        raise ValidationError("The quantity must be at least 1.")


@transaction.atomic
def take_stock(user, medicine, quantity):
    check_quantity(quantity)
    unexpired = medicine.batch_set.filter(expiry_date__gte=timezone.localdate())
    batch = unexpired.filter(quantity__gte=quantity).order_by("expiry_date", "id").first()
    if batch is None:
        largest = unexpired.aggregate(Max("quantity"))["quantity__max"] or 0
        raise ValidationError(
            f"No single batch of {medicine} has {quantity}. The most one batch can give is {largest}."
        )
    batch.quantity -= quantity
    batch.save()
    log_action(user, "Took stock", f"{medicine} · batch {batch.batch_number} · -{quantity}")
    return batch


@transaction.atomic
def return_stock(user, batch, quantity):
    check_quantity(quantity)
    batch.quantity += quantity
    batch.save()
    log_action(user, "Returned stock", f"{batch.medicine} · batch {batch.batch_number} · +{quantity}")
    return batch


@transaction.atomic
def adjust_stock(user, batch, counted_quantity):
    if batch.expiry_date < timezone.localdate():
        raise ValidationError("Expired batches cannot be adjusted. Their quantity is the record of what was lost.")
    if counted_quantity < 0:
        raise ValidationError("The counted quantity cannot be below 0.")
    quantity_before = batch.quantity
    batch.quantity = counted_quantity
    batch.save()
    log_action(
        user,
        "Adjusted stock",
        f"{batch.medicine} · batch {batch.batch_number} · {quantity_before} → {counted_quantity}",
    )
    return batch


def next_batch(medicine):
    batches = medicine.batch_set.filter(expiry_date__gte=timezone.localdate(), quantity__gt=0)
    return batches.order_by("expiry_date", "id").first()


def adjustable_batches(medicine):
    return medicine.batch_set.filter(expiry_date__gte=timezone.localdate()).order_by("expiry_date", "id")


def get_medicine(medicine_id):
    return get_object_or_404(Medicine, pk=medicine_id)


def medicine_with_barcode(barcode):
    return Medicine.objects.filter(barcode=barcode).first()


def find_medicines(search=""):
    medicines = Medicine.objects.select_related("drug_class").order_by("name", "strength")
    if search:
        medicines = medicines.filter(
            Q(name__icontains=search)
            | Q(barcode__icontains=search)
            | Q(batch__batch_number__icontains=search)
        ).distinct()
    return medicines


def stock_row(medicine, days):
    batch = next_batch(medicine)
    days_left = None
    if batch:
        days_left = (batch.expiry_date - timezone.localdate()).days
    return {
        "medicine": medicine,
        "batch": batch,
        "available": available_stock(medicine),
        "last_received": medicine.batch_set.aggregate(Max("received_at"))["received_at__max"],
        "days_left": days_left,
        "near_expiry": days_left is not None and days_left <= days,
    }


def stock_rows(search="", days=NEAR_EXPIRY_DAYS):
    return [stock_row(medicine, days) for medicine in find_medicines(search)]


def is_low(row):
    return row["available"] < row["medicine"].minimum_quantity


def low_stock_rows(days=NEAR_EXPIRY_DAYS, search=""):
    return [row for row in stock_rows(search, days) if is_low(row)]


def low_stock_count():
    return len(low_stock_rows())


def soonest_expiry(row):
    return row["days_left"]


def near_expiry_rows(days=NEAR_EXPIRY_DAYS, search=""):
    rows = [row for row in stock_rows(search, days) if row["near_expiry"]]
    return sorted(rows, key=soonest_expiry)


def near_expiry_count(days=NEAR_EXPIRY_DAYS):
    return len(near_expiry_rows(days))


def medicine_count():
    return Medicine.objects.count()


def medicines_in_stock_count():
    return sum(1 for medicine in Medicine.objects.all() if available_stock(medicine) > 0)


def deliveries_today_count():
    return Batch.objects.filter(received_at__date=timezone.localdate()).count()


def batches_expiring_between(start, end):
    batches = Batch.objects.filter(expiry_date__gte=start, expiry_date__lte=end)
    return batches.order_by("expiry_date", "id")
