from inventory.services import low_stock_rows, near_expiry_rows
from prescriptions.services import UNCOLLECTED_STATUSES, uncollected_rows


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
