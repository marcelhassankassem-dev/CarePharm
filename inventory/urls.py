
from django.urls import path

# Import the view that displays inventory and records deliveries.
from . import views

# Namespace used to identify inventory URLs.
app_name = "inventory"

urlpatterns = [
    # /inventory/ displays the inventory page.
    path("", views.inventory_overview, name="overview"),
]
