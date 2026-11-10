
from django.urls import path

from reports.views import sales_report_page


urlpatterns = [
    path("", sales_report_page, name="sales_report_page"),
]
