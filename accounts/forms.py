from pathlib import Path

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


class FamilyMemberForm(forms.Form):
    username = forms.CharField(label="Their CarePharm username", max_length=150)


class ProfilePhotoForm(forms.Form):
    """Accept only small raster profile pictures; SVG and arbitrary files are unsafe."""

    photo = forms.FileField(widget=forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp"}))

    def clean_photo(self):
        photo = self.cleaned_data["photo"]
        if photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Choose a photo smaller than 2 MB.")

        extension = Path(photo.name).suffix.lower()
        signatures = {
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".png": "png",
            ".webp": "webp",
        }
        photo_type = signatures.get(extension)
        header = photo.read(16)
        photo.seek(0)
        detected = (
            "jpeg" if header.startswith(b"\xff\xd8\xff") else
            "png" if header.startswith(b"\x89PNG\r\n\x1a\n") else
            "webp" if header.startswith(b"RIFF") and header[8:12] == b"WEBP" else None
        )
        if not photo_type or photo_type != detected:
            raise forms.ValidationError("Upload a valid JPG, PNG, or WebP image.")
        return photo
