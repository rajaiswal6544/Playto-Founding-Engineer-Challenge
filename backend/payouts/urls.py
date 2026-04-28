from django.urls import path

from payouts.views import DashboardView, PayoutListCreateView


urlpatterns = [
    path("dashboard", DashboardView.as_view(), name="dashboard"),
    path("payouts", PayoutListCreateView.as_view(), name="payout-list-create"),
]

