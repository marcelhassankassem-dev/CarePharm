from django import forms
from django.utils import timezone


class DelayRefillForm(forms.Form):
    expected_on = forms.DateField(label="Expected on", widget=forms.DateInput(attrs={"type": "date"}))

    def clean_expected_on(self):
        expected_on = self.cleaned_data["expected_on"]
        if expected_on <= timezone.localdate():
            raise forms.ValidationError("The expected date must be after today.")
        return expected_on
