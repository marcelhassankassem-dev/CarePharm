from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import can_act_as_pharmacist, pharmacist_required, staff_required
from passport.services import allergies_of, current_medicines_of
from prescriptions.forms import ItemForm, NewPrescriptionForm
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
    remove_item,
    start_preparing,
    uncollected_count,
    uncollected_rows,
    ready_count,
    ready_rows,
)
from safety.services import safety_warnings
from sales.forms import PaymentForm



# SCRUM-28: Display all uncollected or ready-only prescriptions.
def list_page_data(request):
    search = request.GET.get("search", "")
    show = request.GET.get("show", "all")

    # Show only orders that are ready for pickup.
    if show == "ready":
        rows = ready_rows(search)

    # Otherwise, preserve the existing uncollected list.
    else:
        show = "all"
        rows = uncollected_rows(search)

    return {
        "rows": rows,
        "search": search,
        "show": show,
        "uncollected_count": uncollected_count(),
        "ready_count": ready_count(),
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
    form = NewPrescriptionForm(request.POST) if request.method == "POST" else NewPrescriptionForm()
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
                return render(
                    request,
                    "safety/safety_check.html",
                    {
                        "prescription": prescription,
                        "form": form,
                        "warnings": warnings,
                        "allergies": allergies_of(prescription.patient),
                        "current_medicines": current_medicines_of(prescription.patient),
                    },
                )
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
            messages.error(request, "Check the medicine, quantity and days of supply.")
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
