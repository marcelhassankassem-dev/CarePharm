from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.forms import DeliveryForm
from inventory.services import (
    deliveries_today_count,
    medicines_in_stock_count,
    record_delivery,
    stock_rows,
)


def stock_page_data():
    return {
        "rows": stock_rows(),
        "medicines_in_stock": medicines_in_stock_count(),
        "deliveries_today": deliveries_today_count(),
    }


@staff_required
def inventory_page(request):
    return render(request, "inventory/inventory.html", stock_page_data())


@staff_required
def record_delivery_page(request):
    form = DeliveryForm(request.POST) if request.method == "POST" else DeliveryForm()
    if form.is_valid():
        record_delivery(request.user, **form.cleaned_data)
        messages.success(request, "Delivery recorded.")
        return redirect("inventory_page")
    return render(request, "inventory/delivery.html", {**stock_page_data(), "form": form})
