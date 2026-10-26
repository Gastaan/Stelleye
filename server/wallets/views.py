from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from stellar_bridge.amounts import display
from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError

from . import services
from .models import AnchorTransfer


def _fail(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "message": message}, status=status)


def _amount(request) -> Decimal | None:
    try:
        return Decimal(request.POST.get("amount", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _start(request, kind: str):
    amount = _amount(request)
    if amount is None:
        return _fail("Choose an amount.")
    try:
        low, high = services.transfer_limits(kind)
        if not low <= amount <= high:
            return _fail(f"The payment partner accepts {display(low)} to {display(high)} {get_config().asset_code} at a time.")
        if kind == AnchorTransfer.Kind.WITHDRAW and amount > services.balance_of(request.user):
            return _fail("That is more than you have in your wallet.")
        transfer, url = services.start_transfer(request.user, kind, amount)
    except Exception as exc:  # noqa: BLE001 - anchor and Horizon failures are shown, not raised
        return _fail(str(exc) if isinstance(exc, StellarBridgeError) else "The payment partner did not respond. Please try again.")
    return JsonResponse({"ok": True, "url": url, **services.transfer_view(transfer)})


@require_POST
@login_required
def add_money(request):
    if request.user.profile.is_guide:
        return _fail("Guides receive money from calls; use Cash out to move it to your bank.", 403)
    return _start(request, AnchorTransfer.Kind.DEPOSIT)


@require_POST
@login_required
def cash_out(request):
    if not request.user.profile.is_guide:
        return _fail("Cash out is for guides' earnings.", 403)
    return _start(request, AnchorTransfer.Kind.WITHDRAW)


def _payload(transfer: AnchorTransfer) -> JsonResponse:
    data = {"ok": True, **services.transfer_view(transfer)}
    if data["done"]:
        data["balance"] = display(services.balance_of(transfer.owner))
    return JsonResponse(data)


@require_GET
@login_required
def transfer_status(request, transfer_id):
    transfer = get_object_or_404(AnchorTransfer, pk=transfer_id, owner=request.user)
    if transfer.status not in ("completed", "error", "expired", "refunded"):
        try:
            services.refresh_transfer(transfer)
        except StellarBridgeError as exc:
            return _fail(str(exc), 502)
    return _payload(transfer)


@require_POST
@login_required
def confirm_cash_out(request, transfer_id):
    transfer = get_object_or_404(AnchorTransfer, pk=transfer_id, owner=request.user)
    try:
        services.confirm_withdrawal(transfer)
    except PermissionError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc) if isinstance(exc, StellarBridgeError) else "Sending to the payment partner failed. Nothing was sent.")
    transfer.refresh_from_db()
    return _payload(transfer)
