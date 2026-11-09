from django import forms

from inventory.models import Medicine
from passport.models import Patient
from sales.models import Sale


class NewPrescriptionForm(forms.Form):
    patient = forms.ModelChoiceField(
        queryset=Patient.objects.order_by("name"),
        empty_label="Select a patient",
    )


class ItemForm(forms.Form):
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.order_by("name", "strength"),
        empty_label="Select a medicine",
    )
    quantity = forms.IntegerField(min_value=1)
    days_of_supply = forms.IntegerField(min_value=1)
    long_term = forms.BooleanField(label="Long-term medicine", required=False)


class CollectForm(forms.Form):
    payment_method = forms.ChoiceField(choices=Sale.PAYMENT_CHOICES, widget=forms.RadioSelect)
