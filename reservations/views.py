from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import pharmacist_required, staff_required
from prescriptions.views import list_page_data
from reservations.services import (
    approve_refill,
    collect_reservation,
    expire_reservations,
    get_refill_request,
    get_reservation,
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
