
from django.forms import HiddenInput
from decimal import Decimal

from django import forms
from django.utils import timezone

# Import the Medicine model to get medicines from the database.
from .models import Medicine


# This form collects and validates the information for a new delivery.
class DeliveryForm(forms.Form):


    # Unique identifier for this form submission.
    # A hidden field lets Django recognize repeated requests.
    delivery_request_id = forms.UUIDField(
        widget=HiddenInput(),
        required=True,
    )


    # Allows the pharmacist to select an existing medicine.
    # The medicines are sorted alphabetically by name and strength.
    medicine = forms.ModelChoiceField(
        queryset=Medicine.objects.all().order_by("name", "strength"),
        empty_label="Select a medicine",
    )

    # Name of the company supplying the medicines.
    supplier = forms.CharField(max_length=100)

    # Batch number used to identify the delivered medicine batch.
    batch_number = forms.CharField(max_length=50)

    # Expiry date of the delivered medicines.
    # The widget displays a date picker in the browser.
    expiry_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        input_formats=["%Y-%m-%d"],
    )

    # Number of medicine units received.
    # A delivery must contain at least one unit.
    quantity = forms.IntegerField(
        min_value=1,
        label="Quantity received",
    )

    # Cost of one unit of the delivered medicine.
    # Decimal is used to represent monetary values accurately.
    unit_cost = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    # Django automatically calls this function when validating
    # the expiry_date field.
    def clean_expiry_date(self):

        # Get the expiry date entered by the pharmacist.
        expiry_date = self.cleaned_data["expiry_date"]

        # Reject medicines that are already expired
        # or have an expiry date equal to today.
        if expiry_date <= timezone.localdate():
            raise forms.ValidationError(
                "The expiry date must be in the future."
            )

        # Return the validated expiry date.
        return expiry_date
