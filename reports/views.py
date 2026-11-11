from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import require_owner, staff_required
from inventory.services import NEAR_EXPIRY_DAYS, low_stock_count, near_expiry_count
from reports.services import (
    best_day,
    best_sellers,
    daily_sales,
    monthly_expiry_losses,
    period_expiry_loss,
    prescription_status_counts,
    prescriptions_to_prepare,
    report_period,
    sales_summary,
    stock_alerts,
)
from sales.services import sold_lines_between


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


@staff_required
def sales_report(request):
    require_owner(request.user)
    today = timezone.localdate()
    period = report_period(request.GET.get("period", "month"), today)
    lines = list(sold_lines_between(period["start"], period["end"]))
    days = daily_sales(lines, period["start"], period["end"])
    return render(
        request,
        "reports/sales_report.html",
        {
            "period": period,
            "today": today,
            "summary": sales_summary(lines),
            "today_summary": sales_summary(list(sold_lines_between(today, today))),
            "days": days,
            "best": best_day(days),
            "best_sellers": best_sellers(lines),
            "loss": period_expiry_loss(period, today),
            "losses": monthly_expiry_losses(today),
        },
    )
