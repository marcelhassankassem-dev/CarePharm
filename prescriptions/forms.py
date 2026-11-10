from django import forms

from inventory.models import Medicine


class ItemForm(forms.Form):
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.order_by("name", "strength"),
        empty_label="Select a medicine",
    )
    quantity = forms.IntegerField(min_value=1)
    days_of_supply = forms.IntegerField(min_value=1)
    long_term = forms.BooleanField(label="Long-term medicine", required=False)
