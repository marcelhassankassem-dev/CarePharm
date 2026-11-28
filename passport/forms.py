from django import forms
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.validators import RegexValidator
from django.utils import timezone

from accounts.forms import check_password_rules
from inventory.models import DrugClass, Medicine
from passport.models import Patient

OLDEST_AGE = 120

phone_characters = RegexValidator(r"^[0-9 +-]*$", "Enter a phone number with only digits, spaces, + and -.")


class PatientForm(forms.Form):
    name = forms.CharField(max_length=100)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    phone = forms.CharField(max_length=20, required=False, validators=[phone_characters])
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
    username = forms.CharField(max_length=150, required=False, validators=[UnicodeUsernameValidator()])
    password = forms.CharField(min_length=8, widget=forms.PasswordInput, required=False)

    def clean_date_of_birth(self):
        date_of_birth = self.cleaned_data["date_of_birth"]
        today = timezone.localdate()
        if date_of_birth > today:
            raise forms.ValidationError("The date of birth cannot be in the future.")
        if today.year - date_of_birth.year > OLDEST_AGE:
            raise forms.ValidationError(f"Enter a date of birth within the last {OLDEST_AGE} years.")
        return date_of_birth

    def clean(self):
        cleaned_data = super().clean()
        if "username" in self.errors or "password" in self.errors:
            return cleaned_data
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        if bool(username) != bool(password):
            raise forms.ValidationError(
                "Enter both a username and a temporary password, or leave both empty."
            )
        if password:
            check_password_rules(self, username, password)
        return cleaned_data


class PatientLoginForm(forms.Form):
    username = forms.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = forms.CharField(min_length=8, widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        if username and password:
            check_password_rules(self, username, password)
        return cleaned_data


class PatientChoiceForm(forms.Form):
    patient = forms.ModelChoiceField(
        queryset=Patient.objects.order_by("name"),
        empty_label="Select a patient",
    )


class PassportShareForm(forms.Form):
    include_contact = forms.BooleanField(label="Contact details", required=False)
    include_allergies = forms.BooleanField(label="Allergies", required=False)
    include_medicines = forms.BooleanField(label="Active medicines", required=False)
    include_history = forms.BooleanField(label="Dispensing history", required=False)
    access_days = forms.TypedChoiceField(
        label="Access lasts",
        choices=[(1, "1 day"), (3, "3 days"), (7, "7 days"), (14, "14 days"), (30, "30 days")],
        coerce=int,
        initial=7,
        required=False,
        empty_value=7,
    )
    consent = forms.BooleanField(
        label="I explicitly consent to sharing the selected information with this pharmacy.",
        required=True,
        error_messages={"required": "Confirm your consent before sharing your passport."},
    )

    def clean(self):
        data = super().clean()
        if not any(data.get(key) for key in ("include_contact", "include_allergies", "include_medicines", "include_history")):
            raise forms.ValidationError("Select at least one section to share.")
        return data
