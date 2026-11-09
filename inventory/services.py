from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.services import log_action
from inventory.models import Batch, Medicine


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


def stock_rows():
    rows = []
    for medicine in Medicine.objects.select_related("drug_class").order_by("name", "strength"):
        batches = medicine.batch_set.order_by("expiry_date", "id")
        if not batches:
            rows.append({"medicine": medicine, "batch": None})
        for batch in batches:
            rows.append({"medicine": medicine, "batch": batch})
    return rows


def medicines_in_stock_count():
    return sum(1 for medicine in Medicine.objects.all() if available_stock(medicine) > 0)


def deliveries_today_count():
    return Batch.objects.filter(received_at__date=timezone.localdate()).count()
