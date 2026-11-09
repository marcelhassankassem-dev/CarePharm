from django import forms

from inventory.models import DrugClass, Medicine


class PatientForm(forms.Form):
    name = forms.CharField(max_length=100)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    phone = forms.CharField(max_length=20, required=False)
    allergies = forms.ModelMultipleChoiceField(
        queryset=DrugClass.objects.order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    current_medicines = forms.ModelMultipleChoiceField(
        queryset=Medicine.objects.order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    username = forms.CharField(max_length=150, required=False)
    password = forms.CharField(min_length=8, widget=forms.PasswordInput, required=False)

    def clean(self):
        cleaned_data = super().clean()
        password_was_refused = "password" in self.errors
        if not password_was_refused and bool(cleaned_data.get("username")) != bool(
            cleaned_data.get("password")
        ):
            raise forms.ValidationError(
                "Enter both a username and a temporary password, or leave both empty."
            )
        return cleaned_data


class PatientLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(min_length=8, widget=forms.PasswordInput)
