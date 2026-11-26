from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.permissions import patient_required, pharmacist_required, staff_required
from core.services import pharmacy_details
from passport.views import patient_page_data, selected_patient
from prescriptions.services import patient_long_term_item
from prescriptions.views import list_page_data
from reservations.forms import DelayRefillForm, FindMedicineForm, ReserveForm
from reservations.services import (
    approve_refill,
    collect_reservation,
    delay_refill,
    expire_reservations,
    get_refill_request,
    get_reservation,
    medicine_availability,
    patient_reservation,
    pickup_deadline,
    request_refill,
    reservation_row,
    reserve_medicine,
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
        {**patient_page_data(request), "form": FindMedicineForm()},
    )


@patient_required
def find_medicine_results(request):
    expire_reservations()
    form = FindMedicineForm(request.GET)
    if not form.is_valid():
        return render(request, "reservations/find.html", {**patient_page_data(request), "form": form})
    name = form.cleaned_data["name"]
    quantity = form.cleaned_data["quantity"]
    return render(
        request,
        "reservations/availability.html",
        {
            **patient_page_data(request),
            "name": name,
            "quantity": quantity,
            "results": medicine_availability(name, quantity),
            "pharmacy": pharmacy_details(),
            "can_reserve": selected_patient(request) is not None,
        },
    )


@patient_required
def reserve(request):
    if request.method != "POST":
        return redirect("find_medicine")
    patient = selected_patient(request)
    if patient is None:
        messages.error(
            request,
            "Reservations are made for your own patient record. Please ask the pharmacy to set it up.",
        )
        return redirect("find_medicine")
    form = ReserveForm(request.POST)
    if not form.is_valid():
        return redirect("find_medicine")
    medicine = form.cleaned_data["medicine"]
    quantity = form.cleaned_data["quantity"]
    try:
        reservation = reserve_medicine(request.user, patient, medicine, quantity)
    except ValidationError:
        messages.error(
            request,
            f"Sorry, {medicine} cannot be reserved in that quantity right now. Please ask the pharmacy.",
        )
        return redirect(f"/reservations/find/results/?{urlencode({'name': medicine.name, 'quantity': quantity})}")
    return redirect("reservation_page", reservation.pk)


@patient_required
def reservation_page(request, reservation_id):
    expire_reservations()
    reservation = patient_reservation(selected_patient(request), reservation_id)
    return render(
        request,
        "reservations/reserved.html",
        {
            **patient_page_data(request),
            "reservation": reservation,
            "row": reservation_row(reservation, timezone.now()),
            "deadline": pickup_deadline(reservation),
            "pharmacy": pharmacy_details(),
        },
    )


@patient_required
def refill_request(request, item_id):
    if request.method != "POST":
        return redirect("patient_home")
    item = patient_long_term_item(selected_patient(request), item_id)
    try:
        request_refill(request.user, item)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
    else:
        messages.success(request, f"Refill requested: {item.medicine}. The pharmacy will let you know when it is ready.")
    return redirect("patient_home")