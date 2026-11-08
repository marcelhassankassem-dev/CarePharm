
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Batch
import uuid

# Calculate available stock using only unexpired batches.
def get_available_stock(medicine):

    batches = Batch.objects.filter(
        medicine=medicine,
        expiry_date__gt=timezone.localdate()
    )

    total = batches.aggregate(
        total_quantity=Sum("quantity")
    )["total_quantity"]

    return total or 0


# Record a medicine delivery safely.

# Record a delivery and prevent duplicate submissions.
@transaction.atomic
def record_delivery(
    *,
    medicine,
    supplier,
    batch_number,
    expiry_date,
    quantity,
    unit_cost,
    delivery_request_id=None,
):

    # A delivery must contain at least one stock unit.
    if quantity < 1:
        raise ValidationError(
            "Delivery quantity must be greater than zero."
        )

    # Reject deliveries containing expired medicines.
    if expiry_date <= timezone.localdate():
        raise ValidationError(
            "The expiry date must be in the future."
        )

    # The unit cost must be a positive value.
    if unit_cost <= 0:
        raise ValidationError(
            "Unit cost must be greater than zero."
        )

    # Generate a new ID when a caller does not provide one.
    # This keeps our existing service tests compatible.
    if delivery_request_id is None:
        delivery_request_id = uuid.uuid4()

    # Prepare the information stored in a medicine batch.
    batch_data = {
        "medicine": medicine,
        "supplier": supplier,
        "batch_number": batch_number,
        "expiry_date": expiry_date,
        "quantity": quantity,
        "unit_cost": unit_cost,
    }

    # Validate the batch fields before saving.
    candidate = Batch(
        **batch_data,
        delivery_request_id=delivery_request_id,
    )

    candidate.full_clean(
        exclude=["delivery_request_id"]
    )

    # Create the batch only if its request ID is new.
    # An existing request ID returns the previously saved batch.
    batch, created = Batch.objects.get_or_create(
        delivery_request_id=delivery_request_id,
        defaults=batch_data,
    )

    # Check that a repeated ID refers to the same delivery.
    # Reject attempts to reuse the ID with different information.
    if not created:
        same_delivery = (
            batch.medicine_id == medicine.pk
            and batch.supplier == supplier
            and batch.batch_number == batch_number
            and batch.expiry_date == expiry_date
            and batch.quantity == quantity
            and batch.unit_cost == unit_cost
        )

        if not same_delivery:
            raise ValidationError(
                "This request ID belongs to a different delivery."
            )

    # Return the newly created or previously saved batch.
    return batch