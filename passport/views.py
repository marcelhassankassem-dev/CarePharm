from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import can_act_as_pharmacist, patient_required, staff_required
from accounts.services import create_patient_login
from passport.forms import PassportShareForm, PatientForm, PatientLoginForm
from passport.models import PassportShare
from passport.services import (
    active_passport_share,
    allergies_of,
    chosen_patient,
    create_patient,
    current_medicines_of,
    find_patients,
    get_patient,
    patient_header,
    patient_of,
    revoke_passport_share,
    share_passport,
    viewable_patients,
)
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


@patient_required
def my_passport(request):
    patient = selected_patient(request)
    owner_view = patient is not None and patient.user_id == request.user.pk
    current_share = active_passport_share(patient) if owner_view else None
    share_history = []
    if owner_view:
        now = timezone.now()
        for share in PassportShare.objects.filter(patient=patient).order_by("-consented_at", "-pk"):
            status = (
                "Removed" if share.revoked_at else
                "Expired" if share.expires_at is None or share.expires_at <= now else "Active"
            )
            share_history.append({"share": share, "status": status})
    share_initial = {}
    if current_share:
        share_initial = {
            "include_contact": current_share.include_contact,
            "include_allergies": current_share.include_allergies,
            "include_medicines": current_share.include_medicines,
            "include_history": current_share.include_history,
        }
    return render(
        request,
        "passport/my_passport.html",
        {
            **patient_page_data(request),
            "allergies": allergies_of(patient) if patient else [],
            "current_medicines": current_medicines_of(patient) if patient else [],
            "history": dispensing_history(patient) if patient else [],
            "current_share": current_share,
            "share_form": PassportShareForm(initial=share_initial),
            "share_history": share_history,
            "pharmacy_name": settings.PHARMACY_NAME,
            "pharmacy_address": settings.PHARMACY_ADDRESS,
        },
    )


@patient_required
def passport_share(request):
    patient = patient_of(request.user)
    if patient is None:
        raise PermissionDenied("Only patients can consent to share their own passport.")

    current_share = active_passport_share(patient)
    initial = {}
    if current_share:
        initial = {
            "include_contact": current_share.include_contact,
            "include_allergies": current_share.include_allergies,
            "include_medicines": current_share.include_medicines,
            "include_history": current_share.include_history,
        }
    form = PassportShareForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        share_passport(
            patient,
            request.user,
            include_contact=form.cleaned_data["include_contact"],
            include_allergies=form.cleaned_data["include_allergies"],
            include_medicines=form.cleaned_data["include_medicines"],
            include_history=form.cleaned_data["include_history"],
            access_days=form.cleaned_data["access_days"],
        )
        request.session[VIEWING_PATIENT] = patient.pk
        messages.success(request, f"You shared the selected passport sections with {settings.PHARMACY_NAME}.")
        return redirect("my_passport")
    return render(
        request,
        "passport/share.html",
        {
            **patient_page_data(request),
            "patient": patient,
            "form": form,
            "current_share": current_share,
            "pharmacy_name": settings.PHARMACY_NAME,
            "pharmacy_address": settings.PHARMACY_ADDRESS,
        },
    )


@patient_required
@require_POST
def passport_share_remove(request, share_id):
    patient = patient_of(request.user)
    revoke_passport_share(patient, request.user, share_id)
    request.session[VIEWING_PATIENT] = patient.pk
    messages.success(request, "Pharmacy access removed. CarePharm can no longer view your shared passport details.")
    return redirect("my_passport")


@staff_required
def patient_list(request, patient_id=None):
    search = request.GET.get("search", "")
    patients = find_patients(search)
    selected = get_patient(patient_id) if patient_id else patients.first()
    # Existing staff-created records without a patient login keep their legacy workflow.
    # A logged-in patient's passport requires their explicit section-level consent.
    legacy_record = selected is not None and selected.user_id is None
    share = active_passport_share(selected) if selected and not legacy_record else None
    most_recent_share = (
        PassportShare.objects.filter(patient=selected, pharmacy_name=settings.PHARMACY_NAME)
        .order_by("-consented_at", "-pk")
        .first()
        if selected and not legacy_record and share is None else None
    )
    share_expired = bool(
        most_recent_share
        and most_recent_share.revoked_at is None
        and (most_recent_share.expires_at is None or most_recent_share.expires_at <= timezone.now())
    )
    share_removed = bool(most_recent_share and most_recent_share.revoked_at is not None)
    allow_allergies = bool(legacy_record or (share and share.include_allergies))
    allow_contact = bool(legacy_record or (share and share.include_contact))
    allow_medicines = bool(legacy_record or (share and share.include_medicines))
    allow_history = bool(legacy_record or (share and share.include_history))
    today = timezone.localdate()
    selected_age = None
    if selected and allow_contact:
        birthday = selected.date_of_birth
        selected_age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return render(
        request,
        "passport/patients.html",
        {
            "patients": patients,
            "search": search,
            "selected": selected,
            "selected_share": share,
            "selected_share_expired": share_expired,
            "selected_share_removed": share_removed,
            "selected_latest_share": most_recent_share,
            "allow_contact": allow_contact,
            "allow_allergies": allow_allergies,
            "allow_medicines": allow_medicines,
            "allow_history": allow_history,
            "selected_age": selected_age,
            "can_create_prescription": can_act_as_pharmacist(request.user),
            "allergies": allergies_of(selected) if allow_allergies else [],
            "current_medicines": current_medicines_of(selected) if allow_medicines else [],
            "history": dispensing_history(selected) if allow_history else [],
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
