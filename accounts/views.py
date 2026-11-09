from django.contrib.auth import authenticate, login
from django.shortcuts import redirect, render

from accounts.services import sign_in_problem


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
            return redirect(request.path)
    return render(request, "accounts/login.html", {"side": side, "problem": problem})
