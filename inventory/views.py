
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import DeliveryForm
from .models import Batch, Medicine
from .services import record_delivery


import uuid
from django.core.exceptions import PermissionDenied, ValidationError

# Display inventory information and process new deliveries.
@login_required(login_url="/admin/login/")
def inventory_overview(request):

    # Only authorized pharmacy staff can access inventory.
    allowed_roles = {"owner", "pharmacist", "assistant"}

    if not (
        request.user.is_superuser
        or request.user.role in allowed_roles
    ):
        raise PermissionDenied(
            "You do not have permission to manage inventory."
        )


    # Each newly opened form gets a unique delivery request ID.
    # A submitted form keeps its original ID, even if validation fails.
    if request.method == "POST":
        form = DeliveryForm(request.POST)
    else:
        form = DeliveryForm(
            initial={"delivery_request_id":uuid.uuid4()}
        )



    # Save a valid delivery.
    if request.method == "POST" and form.is_valid():

        try:
            # Record the delivery or retrieve its existing batch
            # if this same request was already processed.
            record_delivery(**form.cleaned_data)

        except ValidationError as error:
            # Display service errors in the delivery popup.
            form.add_error(None, error)

        else:
            messages.success(
                request,
                "Delivery recorded successfully."
            )

            # Redirect after a successful submission.
            return redirect("inventory:overview")


    # Get today's date and the expiry warning threshold.
    today = timezone.localdate()
    expiry_limit = today + timedelta(days=90)

    # Fetch medicines and their batches from the database.
    # Prefetching avoids repeated database queries.
    medicines = list(
        Medicine.objects.prefetch_related("batch_set")
        .order_by("name", "strength")
    )

    # Prepare information needed by the Figma dashboard.
    inventory_rows = []
    in_stock_count = 0
    low_stock_count = 0
    expiring_count = 0



    # Process each medicine to calculate stock and prepare table rows.
    for medicine in medicines:

        # Get all batches belonging to this medicine.
        batches = list(medicine.batch_set.all())

        # Calculate available stock, excluding expired batches.
        available_stock = sum(
            batch.quantity
            for batch in batches
            if batch.expiry_date > today
        )

        # Count medicines that have stock available.
        if available_stock > 0:
            in_stock_count += 1

        # Count medicines below their minimum stock level.
        if available_stock < medicine.minimum_quantity:
            low_stock_count += 1

        # SCRUM-30: Show a medicine even when it has no batches.
        if not batches:
            inventory_rows.append({
                "medicine": medicine,
                "batch": None,
                "available_stock": 0,
                "status": "Out of stock",
            })

        # Process each existing batch.
        for batch in batches:

            # Check whether the batch expires within 90 days.
            near_expiry = (
                batch.quantity > 0
                and today < batch.expiry_date <= expiry_limit
            )

            if near_expiry:
                expiring_count += 1

            # Determine the status of this batch.
            if batch.expiry_date <= today:
                status = "Expired"
            elif available_stock == 0:
                status = "Out of stock"
            elif near_expiry:
                status = "Expiring"
            elif available_stock < medicine.minimum_quantity:
                status = "Low stock"
            else:
                status = "In stock"

            # Add the batch to the inventory table.
            inventory_rows.append({
                "medicine": medicine,
                "batch": batch,
                "available_stock": available_stock,
                "status": status,
            })

    # Count batch records received today.
    deliveries_today = Batch.objects.filter(
        received_at__date=today
    ).count()

    # Send all data to the HTML template.
    return render(
        request,
        "inventory/overview.html",
        {
            "form": form,
            "medicines": medicines,
            "inventory_rows": inventory_rows,
            "in_stock_count": in_stock_count,
            "low_stock_count": low_stock_count,
            "expiring_count": expiring_count,
            "deliveries_today": deliveries_today,
        }
    )
