from django import forms

from sales.models import Sale


class PaymentForm(forms.Form):
    payment_method = forms.ChoiceField(choices=Sale.PAYMENT_CHOICES, widget=forms.RadioSelect)
