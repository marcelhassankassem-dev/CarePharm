from passport.services import allergies_of


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
    return warnings
