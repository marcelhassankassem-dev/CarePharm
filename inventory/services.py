from django.db import transaction
from django.db.models import Max, Q, Sum
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


def next_batch(medicine):
    batches = medicine.batch_set.filter(expiry_date__gte=timezone.localdate(), quantity__gt=0)
    return batches.order_by("expiry_date", "id").first()


def find_medicines(search=""):
    medicines = Medicine.objects.select_related("drug_class").order_by("name", "strength")
    if search:
        medicines = medicines.filter(
            Q(name__icontains=search)
            | Q(barcode__icontains=search)
            | Q(batch__batch_number__icontains=search)
        ).distinct()
    return medicines


def stock_rows(search=""):
    rows = []
    for medicine in find_medicines(search):
        rows.append(
            {
                "medicine": medicine,
                "batch": next_batch(medicine),
                "available": available_stock(medicine),
                "last_received": medicine.batch_set.aggregate(Max("received_at"))["received_at__max"],
            }
        )
    return rows


def medicine_count():
    return Medicine.objects.count()


def medicines_in_stock_count():
    return sum(1 for medicine in Medicine.objects.all() if available_stock(medicine) > 0)


def deliveries_today_count():
    return Batch.objects.filter(received_at__date=timezone.localdate()).count()
