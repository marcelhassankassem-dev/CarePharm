from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.forms import AdjustStockForm, DeliveryForm, StockListForm
from inventory.services import (
    NEAR_EXPIRY_DAYS,
    adjust_stock,
    deliveries_today_count,
    get_medicine,
    low_stock_count,
    low_stock_rows,
    medicine_count,
    medicines_in_stock_count,
    near_expiry_count,
    near_expiry_rows,
    record_delivery,
    stock_rows,
)


def stock_page_data(search="", days=NEAR_EXPIRY_DAYS, show="all"):
    if show == "expiring":
        rows = near_expiry_rows(days, search)
    elif show == "low_stock":
        rows = low_stock_rows(days, search)
    else:
        rows = stock_rows(search, days)
    return {
        "rows": rows,
        "search": search,
        "days": days,
        "show": show,
        "medicine_count": medicine_count(),
        "medicines_in_stock": medicines_in_stock_count(),
        "low_stock_count": low_stock_count(),
        "near_expiry_count": near_expiry_count(days),
        "deliveries_today": deliveries_today_count(),
    }


@staff_required
def inventory_page(request):
    list_form = StockListForm(request.GET)
    list_form.is_valid()
    search = list_form.cleaned_data.get("search", "")
    days = list_form.cleaned_data.get("days") or NEAR_EXPIRY_DAYS
    show = list_form.cleaned_data.get("show") or "all"
    data = stock_page_data(search, days, show)
    return render(request, "inventory/inventory.html", {**data, "list_form": list_form})


@staff_required
def record_delivery_page(request):
    form = DeliveryForm(request.POST) if request.method == "POST" else DeliveryForm()
    if form.is_valid():
        record_delivery(request.user, **form.cleaned_data)
        messages.success(request, "Delivery recorded.")
        return redirect("inventory_page")
    return render(request, "inventory/delivery.html", {**stock_page_data(), "form": form})


@staff_required
def adjust_stock_page(request, medicine_id):
    medicine = get_medicine(medicine_id)
    form = AdjustStockForm(medicine, request.POST) if request.method == "POST" else AdjustStockForm(medicine)
    if form.is_valid():
        batch = adjust_stock(request.user, form.cleaned_data["batch"], form.cleaned_data["counted_quantity"])
        messages.success(request, f"Stock adjusted: {medicine}, batch {batch.batch_number}, now {batch.quantity}.")
        return redirect("inventory_page")
    return render(request, "inventory/adjust.html", {**stock_page_data(), "form": form, "medicine": medicine})
