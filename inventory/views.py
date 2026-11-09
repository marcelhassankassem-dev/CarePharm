from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.forms import DeliveryForm
from inventory.services import (
    deliveries_today_count,
    medicine_count,
    medicines_in_stock_count,
    record_delivery,
    stock_rows,
)


def stock_page_data(search=""):
    return {
        "rows": stock_rows(search),
        "search": search,
        "medicine_count": medicine_count(),
        "medicines_in_stock": medicines_in_stock_count(),
        "deliveries_today": deliveries_today_count(),
    }


@staff_required
def inventory_page(request):
    search = request.GET.get("search", "").strip()
    return render(request, "inventory/inventory.html", stock_page_data(search))


@staff_required
def record_delivery_page(request):
    form = DeliveryForm(request.POST) if request.method == "POST" else DeliveryForm()
    if form.is_valid():
        record_delivery(request.user, **form.cleaned_data)
        messages.success(request, "Delivery recorded.")
        return redirect("inventory_page")
    return render(request, "inventory/delivery.html", {**stock_page_data(), "form": form})
