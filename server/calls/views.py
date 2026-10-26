from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from people.models import LANGUAGES
from stellar_bridge.amounts import display
from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError
from ui.icons import STATUS_BADGES
from wallets import services as wallets
from wallets.models import AnchorTransfer

from . import matching, services
from .models import CallSession

S = CallSession.Status
HIDDEN = [S.RINGING, S.MISSED, S.FAILED]
ADD_MONEY_AMOUNTS = ("5", "10")  # the test anchor accepts 1 to 10 USDC per transfer


def _history(queryset, counterpart: str) -> list[dict]:
    rows = []
    for call in queryset.exclude(status__in=HIDDEN).select_related(f"{counterpart}__profile")[:12]:
        other = getattr(call, counterpart)
        rows.append({**services.summary(call), "with": other.profile.display_name if other else "", "date": call.created_at})
    return rows


# Anchor transfer status -> (badge tone, icon, label); anything else is still moving.
TRANSFER_BADGES = {
    "completed": ("ok", "check", "Completed"),
    "refunded": ("info", "scale", "Refunded"),
    "error": ("danger", "alert", "Did not complete"),
    "expired": ("danger", "alert", "Expired"),
}


def _transactions(user, calls: list[dict]) -> list[dict]:
    """Everything that moved the caller's money, newest first: money added through the anchor, and calls."""
    cfg = get_config()
    rows = []
    for call in calls:
        tone, icon = STATUS_BADGES.get(call["status"], ("muted", "clock"))
        rows.append({
            "kind": "call", "date": call["date"], "title": f"Call with {call['with']}" if call["with"] else "Call",
            "amount": f"−{call['earned']}", "incoming": False,
            "detail": f"{call['seconds']} s · {call['refunded']} returned",
            "tone": tone, "icon": icon, "status_label": call["status_label"], "tx_url": call["close_tx_url"],
        })
    deposits = AnchorTransfer.objects.filter(owner=user, kind=AnchorTransfer.Kind.DEPOSIT).exclude(status="incomplete")
    for transfer in deposits[:15]:
        tone, icon, label = TRANSFER_BADGES.get(transfer.status, ("info", "clock", "In progress"))
        received = transfer.amount_out if transfer.amount_out is not None else transfer.requested_amount
        paid = transfer.amount_in if transfer.amount_in is not None else transfer.requested_amount
        detail = f"{display(paid)} USD by card or bank"
        if transfer.amount_fee:
            detail += f" · fee {display(transfer.amount_fee)}"
        rows.append({
            "kind": "deposit", "date": transfer.created_at, "title": "Added money",
            "amount": f"+{display(received)}", "incoming": True, "detail": detail,
            "tone": tone, "icon": icon, "status_label": label, "tx_url": cfg.tx_url(transfer.stellar_tx),
        })
    rows.sort(key=lambda row: row["date"], reverse=True)
    return rows[:15]


def _guide_totals(calls: list[dict]) -> dict:
    ready = sum(Decimal(c["guide_share"]) for c in calls if c["can_claim"])
    held = sum(Decimal(c["guide_share"]) for c in calls if c["status"] == S.HELD and not c["can_claim"])
    return {"ready": display(ready), "held": display(held)}


def _wallet_context(user) -> dict:
    cfg = get_config()
    try:
        balance = display(wallets.balance_of(user))
    except Exception:  # noqa: BLE001 - Horizon hiccup: draw the page, the live refresh fills it in
        balance = "–"
    return {"balance": balance, "wallet_url": cfg.account_url(user.wallet.public_key),
            "asset_code": cfg.asset_code, "add_money_amounts": ADD_MONEY_AMOUNTS, "anchor_domain": cfg.anchor_domain,
            "inapp_video": settings.SIGHTLINE["inapp_video_test_mode"],
            "call_config": {"ice_servers": settings.SIGHTLINE["ice_servers"], "asset": cfg.asset_code,
                            "user_id": user.id}}


@login_required
def member_home(request):
    profile = getattr(request.user, "profile", None)
    if profile is None or profile.is_guide:
        return redirect("home")
    languages = [(code, name) for code, name in LANGUAGES if profile.speaks(code)] or LANGUAGES
    default_budget = settings.SIGHTLINE["default_budget"]
    return render(request, "calls/member.html", {
        **_wallet_context(request.user),
        "languages": languages,
        "default_budget": default_budget,
        "budgets": sorted({"2.00", default_budget, "5.00", "10.00"}, key=Decimal),
        "transactions": _transactions(request.user, _history(request.user.calls_made.all(), "guide")),
        "guides": matching.guide_directory(request.user.id),
    })


@login_required
def guide_home(request):
    profile = getattr(request.user, "profile", None)
    if profile is None or not profile.is_guide:
        return redirect("home")
    calls = _history(request.user.calls_taken.all(), "member")
    return render(request, "calls/guide.html", {
        **_wallet_context(request.user),
        "calls": calls,
        "totals": _guide_totals(calls),
    })


@login_required
def me(request):
    profile = request.user.profile
    queryset = request.user.calls_taken.all() if profile.is_guide else request.user.calls_made.all()
    calls = _history(queryset, "member" if profile.is_guide else "guide")
    payload = {**_wallet_context(request.user)}
    if profile.is_guide:
        payload["totals"] = _guide_totals(calls)
    else:
        payload["transactions"] = [{**row, "date": row["date"].isoformat()} for row in _transactions(request.user, calls)]
    for call in calls:
        call["date"] = call["date"].isoformat()
    payload["calls"] = calls
    return JsonResponse(payload, json_dumps_params={"default": str})


def _json_action(fn, *args):
    try:
        return JsonResponse({"ok": True, **fn(*args)})
    except (StellarBridgeError, PermissionError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)


@require_POST
@login_required
def confirm(request, call_id):
    return _json_action(services.confirm_call, call_id, request.user.id)


@require_POST
@login_required
def claim(request, call_id):
    return _json_action(services.claim_call, call_id, request.user.id)
