
from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum

from sales.models import Sale, SaleItem


# SCRUM-42: Retrieve sales within an optional date range.
def sales_in_period(start_date=None, end_date=None):
    sales = Sale.objects.all()

    if start_date:
        sales = sales.filter(time__date__gte=start_date)

    if end_date:
        sales = sales.filter(time__date__lte=end_date)

    return sales


# Calculate the main sales report statistics.
def sales_summary(start_date=None, end_date=None):
    sales = sales_in_period(start_date, end_date)

    items = SaleItem.objects.filter(sale__in=sales)

    totals = items.aggregate(
        items_sold=Sum("quantity"),
        revenue=Sum(
            F("quantity") * F("unit_price"),
            output_field=DecimalField(
                max_digits=14,
                decimal_places=2,
            ),
        ),
    )

    return {
        "sales_count": sales.count(),
        "items_sold": totals["items_sold"] or 0,
        "total_revenue": totals["revenue"] or Decimal("0.00"),
    }


# SCRUM-42: Calculate cash and card revenue.
def payment_breakdown(start_date=None, end_date=None):
    sales = sales_in_period(start_date, end_date)

    items = SaleItem.objects.filter(sale__in=sales)

    totals = items.aggregate(
        cash_revenue=Sum(
            F("quantity") * F("unit_price"),
            filter=Q(sale__payment_method="cash"),
            output_field=DecimalField(
                max_digits=14,
                decimal_places=2,
            ),
        ),
        card_revenue=Sum(
            F("quantity") * F("unit_price"),
            filter=Q(sale__payment_method="card"),
            output_field=DecimalField(
                max_digits=14,
                decimal_places=2,
            ),
        ),
    )

    return {
        "cash_revenue": totals["cash_revenue"] or Decimal("0.00"),
        "card_revenue": totals["card_revenue"] or Decimal("0.00"),
    }


# SCRUM-42: Find the best-selling medicines.
def top_selling_medicines(start_date=None, end_date=None, limit=5):
    sales = sales_in_period(start_date, end_date)

    items = SaleItem.objects.filter(sale__in=sales)

    return list(
        items.values(
            "medicine_id",
            "medicine__name",
            "medicine__strength",
        )
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum(
                F("quantity") * F("unit_price"),
                output_field=DecimalField(
                    max_digits=14,
                    decimal_places=2,
                ),
            ),
        )
        .order_by("-quantity_sold", "medicine__name", "medicine__strength")[:limit]
    )
