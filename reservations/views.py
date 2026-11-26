from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import patient_required, pharmacist_required, staff_required
from core.services import pharmacy_details
from passport.services import patient_name_for
from prescriptions.views import list_page_data
from reservations.forms import DelayRefillForm, FindMedicineForm
from reservations.services import (
    approve_refill,
    collect_reservation,
    delay_refill,
    expire_reservations,
    get_refill_request,
    get_reservation,
    medicine_availability,
)
from sales.forms import PaymentForm


@staff_required
def reservation_collect(request, reservation_id):
    expire_reservations()
    reservation = get_reservation(reservation_id)
    form = PaymentForm(request.POST) if request.method == "POST" else PaymentForm()
    if form.is_valid():
        try:
            sale = collect_reservation(request.user, reservation, form.cleaned_data["payment_method"])
        except ValidationError as problem:
            messages.error(request, " ".join(problem.messages))
        else:
            total = reservation.quantity * reservation.medicine.price
            paid_by = sale.get_payment_method_display().lower()
            messages.success(request, f"Reservation collected: Sale {sale.pk}, ${total:.2f}, paid by {paid_by}.")
        return redirect("/prescriptions/?show=reservations")
    return render(
        request,
        "reservations/collect.html",
        {**list_page_data(request, "reservations"), "form": form, "reservation": reservation},
    )


@pharmacist_required
def refill_approve(request, refill_request_id):
    refill_request = get_refill_request(refill_request_id)
    if request.method != "POST":
        return redirect("/prescriptions/?show=refills")
    try:
        prescription = approve_refill(refill_request)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
        return redirect("/prescriptions/?show=refills")
    messages.success(request, f"Refill approved: prescription {prescription.pk} created for {prescription.patient}.")
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def refill_delay(request, refill_request_id):
    refill_request = get_refill_request(refill_request_id)
    form = DelayRefillForm(request.POST) if request.method == "POST" else DelayRefillForm()
    if form.is_valid():
        expected_on = form.cleaned_data["expected_on"]
        patient = refill_request.prescription_item.prescription.patient
        try:
            told = delay_refill(refill_request, expected_on)
        except ValidationError as problem:
            messages.error(request, " ".join(problem.messages))
        else:
            if told:
                messages.success(request, f"Refill delayed until {expected_on:%d %b %Y}. {patient} was told.")
            else:
                messages.success(
                    request,
                    f"Refill delayed until {expected_on:%d %b %Y}. {patient} has no login, so let them know by phone.",
                )
        return redirect("/prescriptions/?show=refills")
    return render(
        request,
        "reservations/refill_delay.html",
        {**list_page_data(request, "refills"), "form": form, "refill_request": refill_request},
    )


@patient_required
def find_medicine(request):
    return render(
        request,
        "reservations/find.html",
        {"patient_name": patient_name_for(request.user), "form": FindMedicineForm()},
    )


@patient_required
def find_medicine_results(request):
    expire_reservations()
    form = FindMedicineForm(request.GET)
    if not form.is_valid():
        return render(request, "reservations/find.html", {"patient_name": patient_name_for(request.user), "form": form})
    name = form.cleaned_data["name"]
    quantity = form.cleaned_data["quantity"]
    return render(
        request,
        "reservations/availability.html",
        {
            "patient_name": patient_name_for(request.user),
            "name": name,
            "quantity": quantity,
            "results": medicine_availability(name, quantity),
            "pharmacy": pharmacy_details(),
        },
    )