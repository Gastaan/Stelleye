from decimal import Decimal

from stellar_sdk import Keypair

from stellar_bridge import accounts
from stellar_bridge.amounts import display
from stellar_bridge.anchor import AnchorClient
from stellar_bridge.config import get_config

from .models import AnchorTransfer, Wallet


def ensure_wallet(user, starting_balance: Decimal = Decimal("0")) -> Wallet:
    """Create (and fund on testnet) the user's wallet if it does not exist yet."""
    existing = Wallet.objects.filter(owner=user).first()
    if existing:
        return existing
    keypair = accounts.provision(starting_balance)
    return Wallet.objects.create(owner=user, public_key=keypair.public_key, secret_seed=keypair.secret)


def keypair_for(user) -> Keypair:
    return user.wallet.keypair()


def balance_of(user) -> Decimal:
    wallet = Wallet.objects.filter(owner=user).first()
    return accounts.balance(wallet.public_key) if wallet else Decimal("0")




# ---- real money in and out through the SEP-24 anchor ------------------------------------

PROCESSING = "Processing with the payment partner."


def _decimal(value) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def transfer_limits(kind: str) -> tuple[Decimal, Decimal]:
    """What the anchor accepts per transfer (testanchor: 1 to 10 USDC)."""
    limits = AnchorClient().limits(kind, get_config().asset_code)
    return Decimal(str(limits.get("min_amount", 1))), Decimal(str(limits.get("max_amount", 10)))


def start_transfer(user, kind: str, amount: Decimal) -> tuple[AnchorTransfer, str]:
    """Open the anchor's hosted page for adding money or cashing out. Returns the record and the page URL."""
    cfg = get_config()
    keypair = keypair_for(user)
    accounts.ensure_trustline(keypair, cfg)
    anchor_id, url = AnchorClient(cfg).start(keypair, kind, str(amount))
    transfer = AnchorTransfer.objects.create(
        owner=user, kind=kind, anchor_domain=cfg.anchor_domain, anchor_id=anchor_id, requested_amount=amount
    )
    return transfer, url


def refresh_transfer(transfer: AnchorTransfer) -> AnchorTransfer:
    tx = AnchorClient().transaction(keypair_for(transfer.owner), transfer.anchor_id)
    transfer.status = tx.status
    transfer.amount_in = _decimal(tx.amount_in)
    transfer.amount_out = _decimal(tx.amount_out)
    transfer.amount_fee = _decimal(tx.amount_fee) if tx.amount_fee else (
        transfer.amount_in - transfer.amount_out if transfer.amount_in is not None and transfer.amount_out is not None else None
    )
    transfer.message = tx.message[:300]
    transfer.stellar_tx = tx.stellar_transaction_id or transfer.stellar_tx
    transfer.save()
    return transfer


def confirm_withdrawal(transfer: AnchorTransfer) -> AnchorTransfer:
    """Send the USDC to the anchor, once, after the person confirmed the amounts."""
    if transfer.kind != AnchorTransfer.Kind.WITHDRAW:
        raise PermissionError("Only a cash-out needs confirming.")
    keypair = keypair_for(transfer.owner)
    tx = AnchorClient().transaction(keypair, transfer.anchor_id)
    if tx.status != "pending_user_transfer_start" or not tx.withdraw_anchor_account:
        raise PermissionError("This cash-out is not ready to send yet.")
    # Claim the payment slot first, so a double tap can never pay twice.
    if not AnchorTransfer.objects.filter(pk=transfer.pk, payment_tx="").update(payment_tx="sending"):
        return refresh_transfer(AnchorTransfer.objects.get(pk=transfer.pk))
    try:
        payment = accounts.pay(keypair, tx.withdraw_anchor_account, tx.amount_in or str(transfer.requested_amount),
                               tx.withdraw_memo, tx.withdraw_memo_type or "text")
    except Exception:
        AnchorTransfer.objects.filter(pk=transfer.pk).update(payment_tx="")
        raise
    AnchorTransfer.objects.filter(pk=transfer.pk).update(payment_tx=payment)
    transfer.refresh_from_db()
    return refresh_transfer(transfer)


def transfer_view(transfer: AnchorTransfer) -> dict:
    """What the page shows and says about a transfer."""
    cfg = get_config()
    deposit = transfer.kind == AnchorTransfer.Kind.DEPOSIT
    status = transfer.status
    needs_confirmation = (not deposit and status == "pending_user_transfer_start"
                          and transfer.payment_tx in ("", None))
    if status == "completed":
        label = (f"Added {display(transfer.amount_out or 0)} {cfg.asset_code} to your wallet." if deposit
                 else f"Sent. {display(transfer.amount_out or 0)} USD is on its way to your bank.")
    elif status == "incomplete":
        label = "Finish the steps on the payment page. This page will update by itself."
    elif needs_confirmation:
        label = (f"Ready to cash out: send {display(transfer.amount_in or 0)} {cfg.asset_code}, "
                 f"you receive {display(transfer.amount_out or 0)} USD (fee {display(transfer.amount_fee or 0)}).")
    elif status == "pending_user_transfer_start":
        label = "Waiting for your payment to the payment partner." if deposit else PROCESSING
    elif status in ("error", "expired", "refunded", "no_market", "too_small", "too_large"):
        label = f"The transfer did not complete. {transfer.message}".strip()
    else:
        label = PROCESSING
    return {
        "id": transfer.pk,
        "kind": transfer.kind,
        "status": status,
        "label": label,
        "done": status in ("completed", "error", "expired", "refunded", "no_market", "too_small", "too_large"),
        "succeeded": status == "completed",  # not "ok": responses use that for "request worked"
        "needs_confirmation": needs_confirmation,
        "amount_in": display(transfer.amount_in) if transfer.amount_in is not None else None,
        "amount_out": display(transfer.amount_out) if transfer.amount_out is not None else None,
        "amount_fee": display(transfer.amount_fee) if transfer.amount_fee is not None else None,
        "tx_url": cfg.tx_url(transfer.stellar_tx if deposit else (transfer.payment_tx if transfer.payment_tx not in ("", "sending") else "")),
    }
