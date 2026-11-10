from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.permissions import staff_required
from accounts.services import create_patient_login
from passport.forms import PatientForm, PatientLoginForm
from passport.services import (
    allergies_of,
    create_patient,
    current_medicines_of,
    find_patients,
    get_patient,
)
from sales.services import dispensing_history


@staff_required
def patient_list(request, patient_id=None):
    search = request.GET.get("search", "")
    patients = find_patients(search)
    selected = get_patient(patient_id) if patient_id else patients.first()
    return render(
        request,
        "passport/patients.html",
        {
            "patients": patients,
            "search": search,
            "selected": selected,
            "allergies": allergies_of(selected),
            "current_medicines": current_medicines_of(selected),
            "history": dispensing_history(selected) if selected else [],
        },
    )


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
