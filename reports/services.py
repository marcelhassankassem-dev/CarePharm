from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from inventory.services import batches_expiring_between, low_stock_rows, near_expiry_rows
from prescriptions.services import UNCOLLECTED_STATUSES, uncollected_rows

PERIOD_LABELS = {"today": "Today", "month": "This month", "last_month": "Last month"}


def prescriptions_to_prepare(status=""):
    rows = uncollected_rows()
    if status:
        rows = [row for row in rows if row["prescription"].status == status]
    return rows


def prescription_status_counts():
    rows = uncollected_rows()
    counts = {"all": len(rows)}
    for status in UNCOLLECTED_STATUSES:
        counts[status] = len([row for row in rows if row["prescription"].status == status])
    return counts


def stock_alert(row, kind):
    return {
        "kind": kind,
        "medicine": row["medicine"],
        "available": row["available"],
        "batch": row["batch"],
        "days_left": row["days_left"],
    }


def stock_alerts():
    low = [stock_alert(row, "low") for row in low_stock_rows()]
    expiring = [stock_alert(row, "expiring") for row in near_expiry_rows()]
    return low + expiring


def report_period(period, today):
    first_of_month = today.replace(day=1)
    if period == "today":
        start, end = today, today
    elif period == "last_month":
        end = first_of_month - timedelta(days=1)
        start = end.replace(day=1)
    else:
        period = "month"
        start, end = first_of_month, today
    return {"key": period, "label": PERIOD_LABELS[period], "start": start, "end": end}


def line_total(line):
    return line.quantity * line.unit_price


def sales_summary(lines):
    total = sum((line_total(line) for line in lines), Decimal("0"))
    sale_ids = {line.sale_id for line in lines}
    return {"total": total, "sale_count": len(sale_ids)}


def daily_sales(lines, start, end):
    totals = {}
    day = start
    while day <= end:
        totals[day] = Decimal("0")
        day += timedelta(days=1)
    for line in lines:
        totals[timezone.localdate(line.sale.time)] += line_total(line)
    highest = max(totals.values())
    days = []
    for day, total in totals.items():
        height = round(total * 100 / highest) if highest else 0
        days.append({"day": day, "total": total, "height": height})
    return days


def day_total(day):
    return day["total"]


def best_day(days):
    best = max(days, key=day_total)
    if not best["total"]:
        return None
    return best


def ranking(medicine_and_units):
    medicine, units = medicine_and_units
    return (-units, str(medicine))


def best_sellers(lines, limit=5):
    units_by_medicine = {}
    for line in lines:
        units_by_medicine[line.medicine] = units_by_medicine.get(line.medicine, 0) + line.quantity
    ranked = sorted(units_by_medicine.items(), key=ranking)[:limit]
    if not ranked:
        return []
    top_units = ranked[0][1]
    return [
        {"medicine": medicine, "units": units, "width": round(units * 100 / top_units)}
        for medicine, units in ranked
    ]


def batch_value(batch):
    return batch.quantity * batch.unit_cost


def expired_stock_loss(start, end, today):
    yesterday = today - timedelta(days=1)
    batches = list(batches_expiring_between(start, min(end, yesterday)))
    return {
        "month": start,
        "items": sum(batch.quantity for batch in batches),
        "value": sum((batch_value(batch) for batch in batches), Decimal("0")),
    }


def period_expiry_loss(period, today):
    return expired_stock_loss(period["end"].replace(day=1), period["end"], today)


def monthly_expiry_losses(today, months=3):
    losses = []
    end = today
    for _ in range(months):
        start = end.replace(day=1)
        losses.append(expired_stock_loss(start, end, today))
        end = start - timedelta(days=1)
    return losses
