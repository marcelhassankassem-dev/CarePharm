from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.forms import EditStaffForm, FamilyMemberForm, NewStaffForm
from accounts.permissions import ROLE_PERMISSIONS, patient_required, require_owner
from accounts.services import (
    active_staff_count,
    add_caregiver,
    caregiver_links_of,
    create_staff_member,
    editable_staff_member,
    landing_page,
    revoke_caregiver,
    sign_in_problem,
    staff_members,
    update_staff_member,
)
from core.services import activity_log, pharmacy_details
from passport.services import patient_name_for, patient_of, patients_cared_for, viewable_patient
from passport.views import VIEWING_PATIENT, patient_page_data, selected_patient
from reservations.services import patient_refill_requests, refill_reminders


def site_root(request):
    if request.user.is_authenticated:
        return redirect(landing_page(request.user))
    return render(request, "accounts/main.html", {"pharmacy": pharmacy_details()})


def patient_login(request):
    return login_page(request, "patient")


def staff_login(request):
    return login_page(request, "staff")


def address_on_this_site(request, address, fallback):
    if url_has_allowed_host_and_scheme(address, allowed_hosts={request.get_host()}):
        return address
    return fallback


def login_page(request, side):
    problem = ""
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username"),
            password=request.POST.get("password"),
        )
        problem = sign_in_problem(user, side)
        if not problem:
            login(request, user)
            return redirect(address_on_this_site(request, request.GET.get("next", ""), landing_page(user)))
    return render(request, "accounts/login.html", {"side": side, "problem": problem})


@patient_required
def patient_home(request):
    patient = selected_patient(request)
    context = patient_page_data(request)
    context["pharmacy"] = pharmacy_details()
    context["refill_reminders"] = refill_reminders(patient)
    context["refill_requests"] = patient_refill_requests(patient)
    return render(request, "accounts/home.html", context)


def family_page_data(request):
    own_record = patient_of(request.user)
    family_members = [
        {"link": link, "name": patient_name_for(link.caregiver)} for link in caregiver_links_of(own_record)
    ]
    return {
        **patient_page_data(request),
        "own_record": own_record,
        "family_members": family_members,
        "cared_for": patients_cared_for(request.user),
    }


@patient_required
def family_access(request):
    return render(request, "accounts/family.html", family_page_data(request))


@patient_required
def family_add(request):
    own_record = patient_of(request.user)
    if own_record is None:
        raise PermissionDenied
    form = FamilyMemberForm(request.POST) if request.method == "POST" else FamilyMemberForm()
    if form.is_valid():
        try:
            link = add_caregiver(own_record, form.cleaned_data["username"])
        except ValidationError as problem:
            form.add_error("username", problem)
        else:
            messages.success(request, f"{patient_name_for(link.caregiver)} can now manage your medicines.")
            return redirect("family_access")
    return render(request, "accounts/family_add.html", {**family_page_data(request), "form": form})


@patient_required
def family_remove(request, link_id):
    if request.method != "POST":
        return redirect("family_access")
    link = revoke_caregiver(patient_of(request.user), link_id)
    messages.success(request, f"{patient_name_for(link.caregiver)} can no longer manage your medicines.")
    return redirect("family_access")


@patient_required
def view_patient(request, patient_id):
    if request.method != "POST":
        return redirect("patient_home")
    patient = viewable_patient(request.user, patient_id)
    request.session[VIEWING_PATIENT] = patient.pk
    return redirect(address_on_this_site(request, request.POST.get("next", ""), "/home/"))


def sign_out(request):
    if request.method == "POST":
        logout(request)
    return redirect("patient_login")


@login_required
def staff_list(request):
    require_owner(request.user)
    show_all = request.GET.get("show") == "all"
    return render(
        request,
        "accounts/staff_list.html",
        {
            "members": staff_members(),
            "active_count": active_staff_count(),
            "role_permissions": ROLE_PERMISSIONS,
            "show_all": show_all,
            "activity": activity_log(today_only=not show_all),
        },
    )


@login_required
def staff_add(request):
    require_owner(request.user)
    if request.method == "POST":
        form = NewStaffForm(request.POST)
        if form.is_valid():
            create_staff_member(**form.cleaned_data)
            return redirect("staff_list")
    else:
        form = NewStaffForm()
    return render(
        request,
        "accounts/staff_form.html",
        {
            "form": form,
            "heading": "Add Staff Member",
            "intro": "Create a sign-in for a pharmacist or an assistant.",
            "button": "Add Staff Member",
        },
    )


@login_required
def staff_edit(request, member_id):
    require_owner(request.user)
    member = editable_staff_member(member_id)
    if request.method == "POST":
        form = EditStaffForm(request.POST)
        if form.is_valid():
            update_staff_member(member, **form.cleaned_data)
            return redirect("staff_list")
    else:
        form = EditStaffForm(initial={"role": member.role, "is_active": member.is_active})
    return render(
        request,
        "accounts/staff_form.html",
        {
            "form": form,
            "heading": member.get_full_name(),
            "intro": "Change this person's role, or turn their account off.",
            "button": "Save Changes",
        },
    )
