from django.urls import path

from . import views

urlpatterns = [
    path("add-money/", views.add_money, name="add_money"),
    path("cash-out/", views.cash_out, name="cash_out"),
    path("transfers/<int:transfer_id>/", views.transfer_status, name="transfer_status"),
    path("transfers/<int:transfer_id>/confirm/", views.confirm_cash_out, name="confirm_cash_out"),
]
