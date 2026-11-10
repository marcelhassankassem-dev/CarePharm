from django.core.exceptions import ValidationError
from django.db import transaction

from core.services import log_action
from passport.services import allergies_of, current_medicines_of
from prescriptions.services import add_item
from safety.models import SafetyOverride


def safety_warnings(patient, medicine):
    warnings = []
    if allergies_of(patient).filter(drug_class=medicine.drug_class).exists():
        warnings.append(
            {
                "kind": "allergy",
                "level": "High",
                "title": "Possible allergy conflict",
                "text": f"Patient has a recorded {medicine.drug_class} allergy. {medicine} may not be safe.",
            }
        )
    if current_medicines_of(patient).filter(medicine=medicine).exists():
        warnings.append(
            {
                "kind": "duplicate",
                "level": "Medium",
                "title": "Duplicate medication check",
                "text": f"Patient already takes {medicine}. Review before dispensing.",
            }
        )
    return warnings


@transaction.atomic
def override_warnings(pharmacist, prescription, medicine, quantity, days_of_supply, long_term, reason):
    reason = reason.strip()
    if not reason:
        raise ValidationError("Enter a reason to continue.")
    warnings = safety_warnings(prescription.patient, medicine)
    item = add_item(prescription, medicine, quantity, days_of_supply, long_term)
    for warning in warnings:
        SafetyOverride.objects.create(
            prescription=prescription,
            medicine=medicine,
            warning_type=warning["kind"],
            reason=reason,
            pharmacist=pharmacist,
        )
    titles = " · ".join(warning["title"] for warning in warnings)
    log_action(pharmacist, "Safety decision", f"{medicine} for {prescription.patient} · {titles} · Reason: {reason}")
    return item
