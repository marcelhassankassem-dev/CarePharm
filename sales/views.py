from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from inventory.services import get_medicine, medicine_with_barcode
from passport.forms import PatientChoiceForm
from passport.services import get_patient
from sales.forms import PaymentForm
from sales.services import (
    add_one,
    complete_sale,
    medicine_cards,
    medicine_match_count,
    remove_one,
    sale_lines,
    sale_total,
)


def current_sale_of(request):
    return request.session.get("current_sale", {})


def sale_patient_of(request):
    patient_id = request.session.get("sale_patient")
    if patient_id is None:
        return None
    return get_patient(patient_id)


def change_current_sale(request, change, medicine):
    current_sale = current_sale_of(request)
    try:
        change(current_sale, medicine)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
        return False
    request.session["current_sale"] = current_sale
    return True


def sale_page_address(search):
    if search:
        return f"/sales/?{urlencode({'search': search})}"
    return "/sales/"


def sale_page_data(request):
    search = request.GET.get("search", "").strip()
    lines = sale_lines(current_sale_of(request))
    return {
        "cards": medicine_cards(search),
        "match_count": medicine_match_count(search),
        "add_on_scan": request.session.get("add_on_scan", False),
        "search": search,
        "lines": lines,
        "in_sale": [line["medicine"].pk for line in lines],
        "total": sale_total(lines),
        "payment_form": PaymentForm(),
        "patient": sale_patient_of(request),
    }


@staff_required
def sale_page(request):
    return render(request, "sales/sales.html", sale_page_data(request))


@staff_required
def sale_patient(request):
    form = PatientChoiceForm(request.POST) if request.method == "POST" else PatientChoiceForm()
    if form.is_valid():
        patient = form.cleaned_data["patient"]
        request.session["sale_patient"] = patient.pk
        messages.success(request, f"{patient} linked to this sale.")
        return redirect("sale_page")
    return render(request, "sales/patient.html", {**sale_page_data(request), "form": form})


@staff_required
def sale_patient_remove(request):
    if request.method == "POST":
        request.session.pop("sale_patient", None)
    return redirect("sale_page")


@staff_required
def sale_add(request, medicine_id):
    medicine = get_medicine(medicine_id)
    if request.method != "POST":
        return redirect("sale_page")
    change_current_sale(request, add_one, medicine)
    return redirect(sale_page_address(request.POST.get("search", "")))


@staff_required
def sale_remove(request, medicine_id):
    medicine = get_medicine(medicine_id)
    if request.method != "POST":
        return redirect("sale_page")
    change_current_sale(request, remove_one, medicine)
    return redirect(sale_page_address(request.POST.get("search", "")))


@staff_required
def sale_scan(request):
    if request.method != "POST":
        return redirect("sale_page")
    code = request.POST.get("search", "").strip()
    add_on_scan = request.POST.get("add_on_scan") == "on"
    request.session["add_on_scan"] = add_on_scan
    medicine = medicine_with_barcode(code)
    if add_on_scan and medicine:
        if change_current_sale(request, add_one, medicine):
            messages.success(request, f"{medicine} added to the sale.")
        return redirect("sale_page")
    return redirect(sale_page_address(code))


@staff_required
def sale_complete(request):
    if request.method != "POST":
        return redirect("sale_page")
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose cash or card.")
        return redirect("sale_page")
    current_sale = current_sale_of(request)
    patient = sale_patient_of(request)
    total = sale_total(sale_lines(current_sale))
    try:
        sale = complete_sale(request.user, form.cleaned_data["payment_method"], current_sale, patient)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
    else:
        request.session["current_sale"] = {}
        request.session.pop("sale_patient", None)
        paid_by = sale.get_payment_method_display().lower()
        linked = f", linked to {patient}" if patient else ""
        messages.success(request, f"Sale {sale.pk} completed: ${total:.2f}, paid by {paid_by}{linked}.")
    return redirect("sale_page")
