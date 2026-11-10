from django.core.exceptions import ValidationError
from django.db import transaction

from core.services import log_action
from inventory.services import available_stock, get_medicine, is_low, stock_rows, take_stock
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


def dispensing_history(patient):
    lines = SaleItem.objects.filter(sale__patient=patient).select_related("sale__staff", "medicine")
    return lines.order_by("-sale__time", "id")


def medicine_cards(search=""):
    return [
        {"medicine": row["medicine"], "available": row["available"], "low": is_low(row)}
        for row in stock_rows(search)
    ]


def add_one(current_sale, medicine):
    key = str(medicine.pk)
    available = available_stock(medicine)
    if current_sale.get(key, 0) >= available:
        raise ValidationError(f"Not enough {medicine} in stock: {available} available.")
    current_sale[key] = current_sale.get(key, 0) + 1


def remove_one(current_sale, medicine):
    key = str(medicine.pk)
    if current_sale.get(key, 0) > 1:
        current_sale[key] -= 1
    else:
        current_sale.pop(key, None)


def sale_lines(current_sale):
    lines = []
    for medicine_id, quantity in current_sale.items():
        medicine = get_medicine(medicine_id)
        available = available_stock(medicine)
        lines.append(
            {
                "medicine": medicine,
                "quantity": quantity,
                "line_total": medicine.price * quantity,
                "available": available,
                "available_after": available - quantity,
            }
        )
    return lines


def sale_total(lines):
    return sum(line["line_total"] for line in lines)


@transaction.atomic
def complete_sale(staff, payment_method, current_sale):
    if not current_sale:
        raise ValidationError("Add at least one medicine first.")
    items = []
    for line in sale_lines(current_sale):
        take_stock(staff, line["medicine"], line["quantity"])
        items.append((line["medicine"], line["quantity"]))
    return record_sale(staff, payment_method, items, "counter")
