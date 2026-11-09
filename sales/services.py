from django.db import transaction

from core.services import log_action
from sales.models import Sale, SaleItem


@transaction.atomic
def record_sale(staff, payment_method, items, source, patient=None):
    sale = Sale.objects.create(
        staff=staff,
        payment_method=payment_method,
        source=source,
        patient=patient,
    )
    for medicine, quantity in items:
        SaleItem.objects.create(
            sale=sale,
            medicine=medicine,
            quantity=quantity,
            unit_price=medicine.price,
        )
    log_action(
        staff,
        "Recorded sale",
        f"Sale {sale.pk} · {sale.get_source_display()} · {sale.get_payment_method_display()}",
    )
    return sale
