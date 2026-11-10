from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from passport.services import allergies_of, current_medicines_of
from safety.forms import OverrideForm
from safety.services import override_warnings


def safety_check_page(request, prescription, form, warnings):
    override_form = OverrideForm()
    if "override" in request.POST:
        override_form = OverrideForm(request.POST)
        if override_form.is_valid():
            data = form.cleaned_data
            try:
                override_warnings(
                    request.user,
                    prescription,
                    data["medicine"],
                    data["quantity"],
                    data["days_of_supply"],
                    data["long_term"],
                    override_form.cleaned_data["reason"],
                )
            except ValidationError as problem:
                messages.error(request, " ".join(problem.messages))
            else:
                messages.success(request, f"Warning overridden. {data['medicine']} added.")
            return redirect("prescription_detail", prescription.pk)
    return render(
        request,
        "safety/safety_check.html",
        {
            "prescription": prescription,
            "form": form,
            "override_form": override_form,
            "warnings": warnings,
            "allergies": allergies_of(prescription.patient),
            "current_medicines": current_medicines_of(prescription.patient),
        },
    )
