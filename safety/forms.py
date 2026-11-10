from django import forms


class OverrideForm(forms.Form):
    reason = forms.CharField(
        label="Pharmacist note",
        widget=forms.Textarea,
        error_messages={"required": "Enter a reason to continue."},
    )
