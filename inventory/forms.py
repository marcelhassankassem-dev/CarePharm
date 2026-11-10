from decimal import Decimal

from django import forms
from django.utils import timezone

from inventory.models import Batch, Medicine


class DeliveryForm(forms.ModelForm):
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.order_by("name", "strength"),
        empty_label="Select a medicine",
    )
    quantity = forms.IntegerField(label="Quantity received", min_value=1)
    unit_cost = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    class Meta:
        model = Batch
        fields = ["medicine", "supplier", "batch_number", "expiry_date", "quantity", "unit_cost"]
        widgets = {"expiry_date": forms.DateInput(attrs={"type": "date"})}

    def clean_expiry_date(self):
        expiry_date = self.cleaned_data["expiry_date"]
        if expiry_date <= timezone.localdate():
            raise forms.ValidationError("The expiry date must be in the future.")
        return expiry_date


class StockListForm(forms.Form):
    SHOW_CHOICES = [
        ("all", "All"),
        ("low_stock", "Low stock"),
        ("expiring", "Near expiry"),
    ]

    search = forms.CharField(required=False)
    days = forms.IntegerField(required=False, min_value=1, max_value=365)
    show = forms.ChoiceField(choices=SHOW_CHOICES, required=False)
