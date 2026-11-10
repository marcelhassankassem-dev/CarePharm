from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.forms import DeliveryForm, StockListForm
from inventory.services import (
    NEAR_EXPIRY_DAYS,
    deliveries_today_count,
    medicine_count,
    medicines_in_stock_count,
    near_expiry_count,
    near_expiry_rows,
    record_delivery,
    stock_rows,
    low_stock_count,
    low_stock_rows,
)



# Prepare the data displayed on the inventory page.
def stock_page_data(search="", days=NEAR_EXPIRY_DAYS, show="all"):

    # Display only medicines approaching expiry.
    if show == "expiring":
        rows = near_expiry_rows(days, search)

    # SCRUM-32: Display medicines below their minimum stock.
    elif show == "low_stock":
        rows = low_stock_rows(search)

    # Otherwise, display all matching medicines.
    else:
        rows = stock_rows(search, days)

    # Return information used by the existing template.
    return {
        "rows": rows,
        "search": search,
        "days": days,
        "show": show,
        "medicine_count": medicine_count(),
        "medicines_in_stock": medicines_in_stock_count(),
        "near_expiry_count": near_expiry_count(days),
        "low_stock_count": low_stock_count(),
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
