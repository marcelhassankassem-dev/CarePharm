from passport.services import allergies_of, current_medicines_of


def safety_warnings(patient, medicine):
    warnings = []
    if allergies_of(patient).filter(drug_class=medicine.drug_class).exists():
        warnings.append(
            {
                "level": "High",
                "title": "Possible allergy conflict",
                "text": f"Patient has a recorded {medicine.drug_class} allergy. {medicine} may not be safe.",
            }
        )
    if current_medicines_of(patient).filter(medicine=medicine).exists():
        warnings.append(
            {
                "level": "Medium",
                "title": "Duplicate medication check",
                "text": f"Patient already takes {medicine}. Review before dispensing.",
            }
        )
    return warnings
