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
    pharmacy_name = forms.CharField(
        label="Pharmacy",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Pharmacy name (optional)"}),
    )
    sections = forms.MultipleChoiceField(
        label="What to include",
        choices=[
            ("contact", "Contact details"),
            ("allergies", "Allergies"),
            ("medicines", "Active medicines"),
            ("history", "Dispensing history"),
        ],
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Choose at least one section to share."},
    )
    days = forms.TypedChoiceField(
        label="Access lasts",
        choices=[(1, "1 day"), (3, "3 days"), (7, "7 days"), (14, "14 days"), (30, "30 days")],
        coerce=int,
        initial=7,
    )
    consent = forms.BooleanField(
        label="I agree to share this medical information with the pharmacy above. I can remove its access at any time.",
        error_messages={"required": "Confirm your consent before sending your passport."},
    )
