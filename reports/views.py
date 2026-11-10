from django.shortcuts import render

from accounts.permissions import staff_required
from inventory.services import NEAR_EXPIRY_DAYS, low_stock_count, near_expiry_count
from reports.services import prescription_status_counts, prescriptions_to_prepare, stock_alerts


@staff_required
def dashboard(request):
    status = request.GET.get("status", "")
    return render(
        request,
        "reports/dashboard.html",
        {
            "status": status,
            "rows": prescriptions_to_prepare(status),
            "counts": prescription_status_counts(),
            "low_stock_count": low_stock_count(),
            "near_expiry_count": near_expiry_count(),
            "near_expiry_days": NEAR_EXPIRY_DAYS,
            "alerts": stock_alerts(),
        },
    )
