
from django.shortcuts import render

from accounts.permissions import require_owner, staff_required
from reports.forms import SalesReportFilterForm
from reports.services import (
    payment_breakdown,
    sales_summary,
    top_selling_medicines,
)


# SCRUM-42: Display the sales reports dashboard.
@staff_required
def sales_report_page(request):
    # Only pharmacy owners can access reports.
    require_owner(request.user)

    form = SalesReportFilterForm(request.GET or None)

    context = {
        "filter_form": form,
        "summary": None,
        "payments": None,
        "top_medicines": [],
    }

    # An empty filter is valid (all-time report).
    # Invalid dates must be corrected before showing results.
    if not form.is_bound or form.is_valid():
        dates = form.cleaned_data if form.is_bound else {}

        start_date = dates.get("start_date")
        end_date = dates.get("end_date")

        context["summary"] = sales_summary(
            start_date, end_date
        )

        context["payments"] = payment_breakdown(
            start_date, end_date
        )

        context["top_medicines"] = top_selling_medicines(
            start_date, end_date
        )

    return render(
        request,
        "reports/sales_report.html",
        context,
    )
