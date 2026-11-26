from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

STAFF_ROLE_CHOICES = [
    ("pharmacist", "Pharmacist"),
    ("assistant", "Assistant"),
]


def check_password_rules(form, username, password):
    try:
        validate_password(password, get_user_model()(username=username))
    except ValidationError as problem:
        form.add_error("password", problem)


class NewStaffForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    password = forms.CharField(
        label="Temporary password",
        min_length=8,
        widget=forms.PasswordInput,
    )
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "username", "password", "role"]

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        if password:
            check_password_rules(self, cleaned_data.get("username"), password)
        return cleaned_data


class EditStaffForm(forms.Form):
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)
    is_active = forms.BooleanField(label="This account can sign in", required=False)
