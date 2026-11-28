from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render

from accounts.permissions import can_act_as_pharmacist, patient_required, staff_required
from accounts.services import caregiver_links_of, create_patient_login
from passport.forms import PassportShareForm, PatientForm, PatientLoginForm
from passport.services import (
    age_of,
    allergies_of,
    chosen_patient,
    create_patient,
    current_medicines_of,
    find_patients,
    get_patient,
    get_share,
    open_shares_of,
    passport_medicines,
    patient_header,
    remove_share,
    share_passport,
    share_rows,
    shared_passport,
    shared_sections,
    viewable_patients,
)
from reservations.services import next_refill_dates
from sales.services import dispensing_history

VIEWING_PATIENT = "viewing_patient_id"


def selected_patient(request):
    return chosen_patient(request.user, request.session.get(VIEWING_PATIENT))


def patient_page_data(request):
    return {
        **patient_header(request.user),
        "viewable_patients": viewable_patients(request.user),
        "viewing": selected_patient(request),
    }


def passport_page_data(request):
    data = {**patient_page_data(request), "pharmacy_name": settings.PHARMACY_NAME}
    patient = data["viewing"]
    if patient:
        data["allergies"] = allergies_of(patient)
        data["medicines"] = passport_medicines(patient, next_refill_dates(patient))
        data["history"] = dispensing_history(patient)
        data["share_rows"] = share_rows(patient)
    return data


@patient_required
def my_passport(request):
    return render(request, "passport/my_passport.html", passport_page_data(request))


@patient_required
def passport_share(request):
    patient = selected_patient(request)
    if patient is None:
        raise Http404
    form = PassportShareForm(request.POST) if request.method == "POST" else PassportShareForm()
    if form.is_valid():
        data = form.cleaned_data
        share = share_passport(patient, data["pharmacy_name"], data["sections"], data["days"])
        messages.success(request, f"Passport sent to {share.pharmacy_name or 'the pharmacy'}. Give them its link.")
        return redirect("my_passport")
    return render(request, "passport/share.html", {**passport_page_data(request), "form": form})


@patient_required
def passport_share_remove(request, share_id):
    share = get_share(selected_patient(request), share_id)
    if request.method == "POST":
        remove_share(share)
        messages.success(request, f"{share.pharmacy_name or 'The pharmacy'} can no longer open your passport.")
        return redirect("my_passport")
    return render(request, "passport/remove_share.html", {**passport_page_data(request), "share": share})


def shared_passport_page(request, token):
    share = shared_passport(token)
    context = {"share": share, "patient": share.patient, **shared_sections(share)}
    return render(request, "passport/shared.html", context)


@staff_required
def patient_list(request, patient_id=None):
    search = request.GET.get("search", "")
    patients = find_patients(search)
    selected = get_patient(patient_id) if patient_id else patients.first()
    context = {
        "patients": patients,
        "search": search,
        "selected": selected,
        "can_create_prescription": can_act_as_pharmacist(request.user),
    }
    if selected:
        context["age"] = age_of(selected)
        context["allergies"] = allergies_of(selected)
        context["current_medicines"] = current_medicines_of(selected)
        context["history"] = dispensing_history(selected)
        context["open_shares"] = open_shares_of(selected)
        context["caregiver_links"] = caregiver_links_of(selected)
    return render(request, "passport/patients.html", context)


@staff_required
def patient_new(request):
    form = PatientForm(request.POST) if request.method == "POST" else PatientForm()
    if form.is_valid():
        data = form.cleaned_data
        try:
            patient = create_patient(
                data["name"],
                data["date_of_birth"],
                phone=data["phone"],
                drug_classes=data["allergies"],
                medicines=data["current_medicines"],
                username=data["username"],
                password=data["password"],
            )
        except ValidationError as problem:
            form.add_error("username", problem)
        else:
            return redirect("patient_detail", patient.pk)
    return render(request, "passport/patient_form.html", {"form": form})


@staff_required
def patient_login_new(request):
    form = PatientLoginForm(request.POST) if request.method == "POST" else PatientLoginForm()
    if form.is_valid():
        username = form.cleaned_data["username"]
        try:
            create_patient_login(username, form.cleaned_data["password"])
        except ValidationError as problem:
            form.add_error("username", problem)
        else:
            messages.success(request, f"Login created for {username}.")
            return redirect("patient_list")
    return render(request, "passport/login_form.html", {"form": form})
