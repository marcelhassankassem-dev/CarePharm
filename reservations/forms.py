from django import forms
from django.utils import timezone

QUANTITY_CHOICES = [(number, number) for number in range(1, 11)]


class FindMedicineForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        error_messages={"required": "Enter a medicine name."},
        widget=forms.TextInput(attrs={"placeholder": "Paracetamol syrup"}),
    )
    quantity = forms.TypedChoiceField(choices=QUANTITY_CHOICES, coerce=int, initial=1)


class DelayRefillForm(forms.Form):
    expected_on = forms.DateField(label="Expected on", widget=forms.DateInput(attrs={"type": "date"}))

    def clean_expected_on(self):
        expected_on = self.cleaned_data["expected_on"]
        if expected_on <= timezone.localdate():
            raise forms.ValidationError("The expected date must be after today.")
        return expected_on
