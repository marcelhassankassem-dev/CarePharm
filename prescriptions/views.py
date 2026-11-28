from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect, render

from accounts.models import CaregiverLink
from accounts.permissions import can_act_as_pharmacist, patient_required, pharmacist_required, staff_required
from passport.forms import PatientChoiceForm
from passport.views import patient_page_data, selected_patient
from prescriptions.forms import ItemForm
from prescriptions.services import (
    add_item,
    create_prescription,
    delete_prescription,
    get_item,
    get_prescription,
    items_of,
    mark_collected,
    mark_ready,
    mark_returned,
    prescriptions_of,
    ready_count,
    ready_rows,
    remove_item,
    start_preparing,
    status_steps,
    uncollected_count,
    uncollected_rows,
)
from reservations.services import (
    active_reservation_count,
    expire_reservations,
    new_refill_count,
    open_refill_count,
    refill_rows,
    reservation_rows,
)
from reservations.models import RefillRequest
from safety.services import safety_warnings
from safety.views import safety_check_page
from sales.forms import PaymentForm


def list_page_data(request, show=""):
    expire_reservations()
    search = request.GET.get("search", "")
    show = show or request.GET.get("show", "all")
    if show == "ready":
        rows = ready_rows(search)
    elif show == "reservations":
        rows = reservation_rows(search)
    elif show == "refills":
        rows = refill_rows(search)
    else:
        show = "all"
        rows = uncollected_rows(search)
    return {
        "rows": rows,
        "search": search,
        "show": show,
        "uncollected_count": uncollected_count(),
        "ready_count": ready_count(),
        "reservation_count": active_reservation_count(),
        "panel_reservations": reservation_rows()[:4],
        "refill_count": open_refill_count(),
        "new_refill_count": new_refill_count(),
        "panel_refills": refill_rows()[:3],
        "can_change": can_act_as_pharmacist(request.user),
    }


def run_action(request, action, *args):
    if request.method != "POST":
        return False
    try:
        action(*args)
    except ValidationError as problem:
        messages.error(request, " ".join(problem.messages))
        return False
    return True


@staff_required
def prescription_list(request):
    return render(request, "prescriptions/prescriptions.html", list_page_data(request))


@pharmacist_required
def prescription_new(request):
    if request.method == "POST":
        form = PatientChoiceForm(request.POST)
    else:
        patient_id = request.GET.get("patient", "")
        initial = {"patient": int(patient_id)} if patient_id.isdecimal() else None
        form = PatientChoiceForm(initial=initial)
    if form.is_valid():
        prescription = create_prescription(form.cleaned_data["patient"])
        return redirect("prescription_detail", prescription.pk)
    return render(request, "prescriptions/new.html", {**list_page_data(request), "form": form})


@staff_required
def prescription_detail(request, prescription_id):
    prescription = get_prescription(prescription_id)
    return render(
        request,
        "prescriptions/prescription.html",
        {
            "prescription": prescription,
            "items": items_of(prescription),
            "can_change": can_act_as_pharmacist(request.user),
            "item_form": ItemForm(),
            "collect_form": PaymentForm(),
        },
    )


@pharmacist_required
def prescription_add_item(request, prescription_id):
    prescription = get_prescription(prescription_id)
    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            warnings = safety_warnings(prescription.patient, data["medicine"])
            if warnings:
                return safety_check_page(request, prescription, form, warnings)
            added = run_action(
                request,
                add_item,
                prescription,
                data["medicine"],
                data["quantity"],
                data["days_of_supply"],
                data["long_term"],
            )
            if added:
                messages.success(request, f"No safety alerts. {data['medicine']} added.")
        else:
            reasons = ["Check the medicine, quantity and days of supply."]
            for field_errors in form.errors.values():
                reasons.extend(field_errors)
            messages.error(request, " ".join(reasons))
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def prescription_remove_item(request, item_id):
    item = get_item(item_id)
    run_action(request, remove_item, item)
    return redirect("prescription_detail", item.prescription.pk)


@pharmacist_required
def prescription_delete(request, prescription_id):
    prescription = get_prescription(prescription_id)
    if run_action(request, delete_prescription, prescription):
        messages.success(request, "Prescription deleted.")
        return redirect("prescription_list")
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def prescription_preparing(request, prescription_id):
    prescription = get_prescription(prescription_id)
    run_action(request, start_preparing, prescription)
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def prescription_ready(request, prescription_id):
    prescription = get_prescription(prescription_id)
    run_action(request, mark_ready, request.user, prescription)
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def prescription_collected(request, prescription_id):
    prescription = get_prescription(prescription_id)
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment_method = form.cleaned_data["payment_method"]
            run_action(request, mark_collected, request.user, prescription, payment_method)
        else:
            messages.error(request, "Choose cash or card.")
    return redirect("prescription_detail", prescription.pk)


@pharmacist_required
def prescription_returned(request, prescription_id):
    prescription = get_prescription(prescription_id)
    run_action(request, mark_returned, request.user, prescription)
    return redirect("prescription_detail", prescription.pk)


def tracked_prescriptions(patient):
    return [
        {"prescription": prescription, "items": list(items_of(prescription)), "steps": status_steps(prescription)}
        for prescription in prescriptions_of(patient)
    ]


@patient_required
def my_prescriptions(request):
    patient = selected_patient(request)
    context = patient_page_data(request)
    context["rows"] = tracked_prescriptions(patient)
    caregiver_view = patient is not None and CaregiverLink.objects.filter(
        patient=patient, caregiver=request.user, active=True
    ).exists()
    context["caregiver_return"] = caregiver_view
    context["caregiver_refill_updates"] = (
        list(
            RefillRequest.objects.filter(prescription_item__prescription__patient=patient)
            .filter(
                Q(status__in=["waiting", "delayed"])
                | Q(status="approved", new_prescription__status__in=["received", "preparing", "ready"])
            )
            .select_related("prescription_item__medicine", "new_prescription")
            .order_by("-created_at", "-pk")
        ) if caregiver_view else []
    )
    return render(request, "prescriptions/mine.html", context)
