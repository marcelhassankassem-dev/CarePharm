from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import EditStaffForm, NewStaffForm
from accounts.permissions import ROLE_PERMISSIONS, is_owner, require_owner
from accounts.services import (
    active_staff_count,
    create_staff_member,
    editable_staff_member,
    sign_in_problem,
    staff_members,
    update_staff_member,
)
from core.services import activity_log


def site_root(request):
    return redirect("patient_login")


def patient_login(request):
    return login_page(request, "patient")


def staff_login(request):
    return login_page(request, "staff")


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
            if is_owner(user):
                return redirect("staff_list")
            return redirect(request.path)
    return render(request, "accounts/login.html", {"side": side, "problem": problem})


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
