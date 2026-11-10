from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.services import get_medicine
from sales.forms import PaymentForm
from sales.services import add_one, complete_sale, medicine_cards, remove_one, sale_lines, sale_total


def current_sale_of(request):
    return request.session.get("current_sale", {})


def change_current_sale(request, change, medicine):
    current_sale = current_sale_of(request)
    try:
        change(current_sale, medicine)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
    request.session["current_sale"] = current_sale


@staff_required
def sale_page(request):
    search = request.GET.get("search", "")
    lines = sale_lines(current_sale_of(request))
    return render(
        request,
        "sales/sales.html",
        {
            "cards": medicine_cards(search),
            "search": search,
            "lines": lines,
            "in_sale": [line["medicine"].pk for line in lines],
            "total": sale_total(lines),
            "payment_form": PaymentForm(),
        },
    )


@staff_required
def sale_add(request, medicine_id):
    medicine = get_medicine(medicine_id)
    if request.method == "POST":
        change_current_sale(request, add_one, medicine)
    return redirect("sale_page")


@staff_required
def sale_remove(request, medicine_id):
    medicine = get_medicine(medicine_id)
    if request.method == "POST":
        change_current_sale(request, remove_one, medicine)
    return redirect("sale_page")


@staff_required
def sale_complete(request):
    if request.method != "POST":
        return redirect("sale_page")
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose cash or card.")
        return redirect("sale_page")
    current_sale = current_sale_of(request)
    total = sale_total(sale_lines(current_sale))
    try:
        sale = complete_sale(request.user, form.cleaned_data["payment_method"], current_sale)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
    else:
        request.session["current_sale"] = {}
        paid_by = sale.get_payment_method_display().lower()
        messages.success(request, f"Sale {sale.pk} completed: ${total:.2f}, paid by {paid_by}.")
    return redirect("sale_page")
