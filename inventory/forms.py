from decimal import Decimal

from django import forms
from django.utils import timezone

from inventory.models import Batch, DrugClass, Medicine
from inventory.services import adjustable_batches, batch_number_used


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

    def clean(self):
        cleaned_data = super().clean()
        medicine = cleaned_data.get("medicine")
        batch_number = cleaned_data.get("batch_number")
        if medicine and batch_number and batch_number_used(medicine, batch_number):
            self.add_error("batch_number", f"Batch {batch_number} is already recorded for {medicine}.")
        return cleaned_data


class MedicineForm(forms.ModelForm):
    drug_class = forms.ModelChoiceField(
        queryset=DrugClass.objects.order_by("name"),
        empty_label="Select a drug class",
    )
    price = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    class Meta:
        model = Medicine
        fields = ["name", "strength", "barcode", "drug_class", "price", "minimum_quantity"]


class PriceForm(forms.Form):
    price = forms.DecimalField(label="New price", max_digits=8, decimal_places=2, min_value=Decimal("0.01"))


class BatchChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, batch):
        return f"{batch.batch_number} · expires {batch.expiry_date:%d %b %Y} · {batch.quantity} in stock"


class AdjustStockForm(forms.Form):
    batch = BatchChoiceField(queryset=Batch.objects.none(), empty_label=None)
    counted_quantity = forms.IntegerField(label="Quantity counted on the shelf", min_value=0)

    def __init__(self, medicine, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = adjustable_batches(medicine)


class StockListForm(forms.Form):
    SHOW_CHOICES = [
        ("all", "All"),
        ("low_stock", "Low stock"),
        ("expiring", "Near expiry"),
    ]

    search = forms.CharField(required=False)
    days = forms.IntegerField(required=False, min_value=1, max_value=365)
    show = forms.ChoiceField(choices=SHOW_CHOICES, required=False)
    all = forms.BooleanField(required=False)
