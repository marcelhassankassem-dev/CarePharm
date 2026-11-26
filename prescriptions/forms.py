from django import forms

from inventory.models import Medicine


class ItemForm(forms.Form):
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.order_by("name", "strength"),
        empty_label="Select a medicine",
    )
    quantity = forms.IntegerField(
        min_value=1,
        max_value=1000,
        error_messages={"max_value": "The quantity can be at most 1,000."},
    )
    days_of_supply = forms.IntegerField(
        min_value=1,
        max_value=365,
        error_messages={"max_value": "Days of supply can be at most 365."},
    )
    long_term = forms.BooleanField(label="Long-term medicine", required=False)
