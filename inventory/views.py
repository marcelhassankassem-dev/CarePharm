from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from accounts.permissions import can_add_medicines, is_owner, require_owner, staff_required
from inventory.forms import AdjustStockForm, DeliveryForm, MedicineForm, PriceForm, StockListForm
from inventory.services import (
    MEDICINES_PER_PAGE,
    NEAR_EXPIRY_DAYS,
    add_medicine,
    adjust_stock,
    change_price,
    deliveries_today_count,
    get_medicine,
    low_stock_count,
    low_stock_rows,
    matching_medicine_count,
    medicine_count,
    medicines_in_stock_count,
    near_expiry_count,
    near_expiry_rows,
    record_delivery,
    stock_rows,
)
from reservations.services import expire_reservations


def stock_page_data(user, search="", days=NEAR_EXPIRY_DAYS, show="all", show_all=False):
    expire_reservations()
    limit = None if show_all else MEDICINES_PER_PAGE
    if show == "expiring":
        rows = near_expiry_rows(days, search)
        matching_count = len(rows)
    elif show == "low_stock":
        rows = low_stock_rows(days, search)
        matching_count = len(rows)
    else:
        rows = stock_rows(search, days, limit)
        matching_count = matching_medicine_count(search)
    rows = rows[:limit]
    return {
        "rows": rows,
        "has_more": len(rows) < matching_count,
        "search": search,
        "days": days,
        "show": show,
        "medicine_count": medicine_count(),
        "medicines_in_stock": medicines_in_stock_count(),
        "low_stock_count": low_stock_count(),
        "near_expiry_count": near_expiry_count(days),
        "deliveries_today": deliveries_today_count(),
        "can_add_medicines": can_add_medicines(user),
        "can_change_prices": is_owner(user),
    }


@staff_required
def inventory_page(request):
    list_form = StockListForm(request.GET)
    list_form.is_valid()
    search = list_form.cleaned_data.get("search", "")
    days = list_form.cleaned_data.get("days") or NEAR_EXPIRY_DAYS
    show = list_form.cleaned_data.get("show") or "all"
    show_all = list_form.cleaned_data.get("all", False)
    data = stock_page_data(request.user, search, days, show, show_all)
    return render(request, "inventory/inventory.html", {**data, "list_form": list_form})


@staff_required
def new_medicine_page(request):
    if not can_add_medicines(request.user):
        raise PermissionDenied
    form = MedicineForm(request.POST) if request.method == "POST" else MedicineForm()
    if form.is_valid():
        medicine = add_medicine(request.user, **form.cleaned_data)
        messages.success(request, f"{medicine} added. Record its first delivery.")
        return redirect(f"/inventory/delivery/?medicine={medicine.pk}")
    return render(request, "inventory/new_medicine.html", {**stock_page_data(request.user), "form": form})


@staff_required
def record_delivery_page(request):
    if request.method == "POST":
        form = DeliveryForm(request.POST)
    else:
        form = DeliveryForm(initial={"medicine": request.GET.get("medicine")})
    if form.is_valid():
        record_delivery(request.user, **form.cleaned_data)
        messages.success(request, "Delivery recorded.")
        return redirect("inventory_page")
    return render(request, "inventory/delivery.html", {**stock_page_data(request.user), "form": form})


@staff_required
def adjust_stock_page(request, medicine_id):
    medicine = get_medicine(medicine_id)
    form = AdjustStockForm(medicine, request.POST) if request.method == "POST" else AdjustStockForm(medicine)
    if form.is_valid():
        batch = adjust_stock(request.user, form.cleaned_data["batch"], form.cleaned_data["counted_quantity"])
        messages.success(request, f"Stock adjusted: {medicine}, batch {batch.batch_number}, now {batch.quantity}.")
        return redirect("inventory_page")
    context = {**stock_page_data(request.user), "form": form, "medicine": medicine}
    return render(request, "inventory/adjust.html", context)


@staff_required
def change_price_page(request, medicine_id):
    require_owner(request.user)
    medicine = get_medicine(medicine_id)
    form = PriceForm(request.POST) if request.method == "POST" else PriceForm()
    if form.is_valid():
        change_price(request.user, medicine, form.cleaned_data["price"])
        messages.success(request, f"{medicine} now costs ${medicine.price}.")
        return redirect("inventory_page")
    context = {**stock_page_data(request.user), "form": form, "medicine": medicine}
    return render(request, "inventory/price.html", context)
