from django import forms
from django.contrib.auth import get_user_model

STAFF_ROLE_CHOICES = [
    ("pharmacist", "Pharmacist"),
    ("assistant", "Assistant"),
]


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


class EditStaffForm(forms.Form):
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)
    is_active = forms.BooleanField(label="This account can sign in", required=False)
