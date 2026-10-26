"""Call lifecycle. Synchronous on purpose: the WebSocket consumer runs these in worker threads.

State machine (mirrors CallEscrow on-chain):
    ringing -> connecting -> active -> ending -> held -> released
                                                  \\-> disputed -> resolved
    ringing -> missed            connecting -> failed
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from stellar_bridge.amounts import display
from stellar_bridge.config import get_config
from stellar_bridge.escrow import EscrowClient
from wallets.services import keypair_for

from . import channel
from .models import CallSession, ChatMessage

S = CallSession.Status
LIVE = [S.RINGING, S.CONNECTING, S.ACTIVE, S.ENDING]
CHAT_OPEN = [S.CONNECTING, S.ACTIVE, S.ENDING]
SIGNAL_OPEN = [S.CONNECTING, S.ACTIVE]
# A call still ringing or connecting this long after it was requested was cut off by a server restart.
STALE_AFTER = {S.RINGING: timedelta(minutes=2), S.CONNECTING: timedelta(minutes=4)}
MAX_MESSAGE = 500


def _call(call_id: int) -> CallSession:
    return CallSession.objects.select_related("member__profile", "guide__profile").get(pk=call_id)


def escrow_for(call: CallSession) -> EscrowClient:
    """The CallEscrow contract this call lives on; older calls stay on the contract they were opened with."""
    cfg = get_config()
    return EscrowClient(cfg.with_overrides(escrow_contract_id=call.escrow_contract or cfg.escrow_contract_id))


def guide_share(amount: Decimal) -> Decimal:
    """What the guide receives after the platform fee (same rounding as the contract)."""
    fee = (amount * get_config().fee_bps / Decimal(10_000)).quantize(Decimal("0.0000001"))
    return amount - fee


def max_seconds(call: CallSession) -> int:
    return int(call.budget / call.rate_per_minute * 60) if call.rate_per_minute else 0


# ---- matching ------------------------------------------------------------------


def create_call(member_id: int, language: str, budget: Decimal, direct: bool = False) -> CallSession:
    return CallSession.objects.create(member_id=member_id, language=language, budget=budget, direct=direct)


def assign_guide(call_id: int, guide_user_id: int) -> dict:
    with transaction.atomic():
        call = CallSession.objects.select_for_update().get(pk=call_id)
        call.guide_id = guide_user_id
        call.rate_per_minute = call.guide.profile.rate_per_minute
        call.status = S.RINGING
        call.save(update_fields=["guide", "rate_per_minute", "status"])
    call = _call(call_id)
    return incoming_info(call)


def incoming_info(call: CallSession) -> dict:
    return {
        "member_id": call.member_id,
        "member_name": call.member.profile.display_name,
        "member_initials": call.member.profile.initials,
        "guide_intro": call.guide.profile.spoken_intro,
        "guide_name": call.guide.profile.display_name,
        "language": call.language,
        "rate": display(call.rate_per_minute),
        "budget": display(call.budget),
        "direct": call.direct,
    }


def is_ringing(call_id: int, guide_user_id: int) -> bool:
    return CallSession.objects.filter(pk=call_id, guide_id=guide_user_id, status=S.RINGING).exists()


def is_direct(call_id: int) -> bool:
    return CallSession.objects.filter(pk=call_id, direct=True).exists()


def record_decline(call_id: int, guide_user_id: int) -> bool:
    """Returns True if the decline counted (the call was ringing this guide)."""
    with transaction.atomic():
        call = CallSession.objects.select_for_update().get(pk=call_id)
        if call.status != S.RINGING or call.guide_id != guide_user_id:
            return False
        call.declined_by.add(guide_user_id)
        call.guide_id = None
        call.save(update_fields=["guide"])
    return True


def cancel_ringing(call_id: int, member_id: int) -> int | None:
    """Member hangs up before anyone answers. Returns the guide that was ringing."""
    with transaction.atomic():
        call = CallSession.objects.select_for_update().get(pk=call_id, member_id=member_id)
        if call.status != S.RINGING:
            return None
        guide_id = call.guide_id
        call.status = S.MISSED
        call.save(update_fields=["status"])
    return guide_id


def mark(call_id: int, status: str) -> None:
    CallSession.objects.filter(pk=call_id).update(status=status)


# ---- on-chain lifecycle ------------------------------------------------------------


def begin_connecting(call_id: int, guide_user_id: int) -> dict | None:
    with transaction.atomic():
        call = CallSession.objects.select_for_update().get(pk=call_id)
        if call.status != S.RINGING or call.guide_id != guide_user_id:
            return None
        call.status = S.CONNECTING
        call.save(update_fields=["status"])
    call = _call(call_id)
    note(call_id, f"{call.guide.profile.display_name} accepted the call.")
    return {"member_id": call.member_id, "guide_id": call.guide_id}


def start_call(call_id: int) -> dict:
    """Lock the member's budget in CallEscrow. Billing starts at this ledger's timestamp."""
    call = _call(call_id)
    call.escrow_contract = get_config().escrow_contract_id
    session_id, tx_hash = escrow_for(call).open(
        keypair_for(call.member), call.guide.wallet.public_key, call.rate_per_minute, call.budget
    )
    call.onchain_id = session_id
    call.open_tx = tx_hash
    call.status = S.ACTIVE
    call.started_at = timezone.now()
    call.save(update_fields=["escrow_contract", "onchain_id", "open_tx", "status", "started_at"])
    note(call_id, f"Up to {display(call.budget)} {get_config().asset_code} locked on Stellar. Billing started.",
         get_config().tx_url(tx_hash))
    return {"member_id": call.member_id, "guide_id": call.guide_id}


def begin_ending(call_id: int) -> tuple[int, int] | None:
    with transaction.atomic():
        call = CallSession.objects.select_for_update().get(pk=call_id)
        if call.status != S.ACTIVE:
            return None
        call.status = S.ENDING
        call.save(update_fields=["status"])
    return call.member_id, call.guide_id


def end_call(call_id: int, ended_by_user_id: int, recording_hash: bytes | None = None) -> dict:
    """Close on-chain: refund unused budget, start the hold on the guide's earnings."""
    call = _call(call_id)
    ender = call.member if ended_by_user_id == call.member_id else call.guide
    client = escrow_for(call)
    earned, tx_hash = client.close(keypair_for(ender), call.onchain_id, recording_hash)
    onchain = client.get_session(call.onchain_id)
    cfg = get_config()

    call.earned = earned
    call.refunded = call.budget - earned
    call.close_tx = tx_hash
    call.started_at = onchain.started_at or call.started_at
    call.ended_at = onchain.ended_at or timezone.now()
    if earned > 0:
        call.status = S.HELD
        call.hold_until = call.ended_at + timedelta(seconds=cfg.hold_seconds)
    else:
        call.status = S.RELEASED
    call.save()
    note(call_id, f"Call settled on Stellar: {display(earned)} {cfg.asset_code} paid, "
                  f"{display(call.refunded)} {cfg.asset_code} returned.", cfg.tx_url(tx_hash))
    return summary(call)


def confirm_call(call_id: int, member_id: int) -> dict:
    call = _call(call_id)
    if call.member_id != member_id:
        raise PermissionError("Only the caller can release payment.")
    call.payout_tx = escrow_for(call).confirm(keypair_for(call.member), call.onchain_id)
    call.guide_payout = guide_share(call.earned)
    call.status = S.RELEASED
    call.save(update_fields=["payout_tx", "guide_payout", "status"])
    return summary(call)


def claim_call(call_id: int, guide_id: int) -> dict:
    call = _call(call_id)
    if call.guide_id != guide_id:
        raise PermissionError("Only the guide can withdraw these earnings.")
    amount, call.payout_tx = escrow_for(call).claim(keypair_for(call.guide), call.onchain_id)
    call.guide_payout = amount
    call.status = S.RELEASED
    call.save(update_fields=["payout_tx", "guide_payout", "status"])
    return summary(call)


# ---- chat and signalling ---------------------------------------------------------------


def message_payload(message: ChatMessage, call: CallSession) -> dict:
    if message.sender_id is None:
        role, name = "system", "Stelleye"
    elif message.sender_id == call.member_id:
        role, name = "member", call.member.profile.display_name
    else:
        role, name = "guide", call.guide.profile.display_name
    return {
        "id": message.id,
        "call_id": call.pk,
        "role": role,
        "name": name,
        "text": message.body,
        "link": message.link,
        "at": message.created_at.isoformat(),
    }


def note(call_id: int, body: str, link: str = "") -> dict:
    """A system line in the call's chat, e.g. a payment receipt."""
    message = ChatMessage.objects.create(call_id=call_id, body=body[:MAX_MESSAGE], link=link)
    return message_payload(message, _call(call_id))


def post_message(call_id: int, sender_id: int, text: str) -> dict | None:
    """Store a chat line from one of the two participants while the call is open."""
    body = " ".join(str(text or "").split())[:MAX_MESSAGE]
    if not body:
        return None
    call = _call(call_id)
    if sender_id not in (call.member_id, call.guide_id) or call.status not in CHAT_OPEN:
        return None
    message = ChatMessage.objects.create(call=call, sender_id=sender_id, body=body)
    return message_payload(message, call)


def transcript(call_id: int) -> list[dict]:
    call = _call(call_id)
    return [message_payload(m, call) for m in call.messages.all()]


def latest_note(call_id: int) -> dict | None:
    call = _call(call_id)
    message = call.messages.filter(sender__isnull=True).last()
    return message_payload(message, call) if message else None


def signal_peer(call_id: int, user_id: int) -> int | None:
    """The other participant's user id, if `user_id` may send them video signalling now."""
    call = CallSession.objects.filter(pk=call_id, status__in=SIGNAL_OPEN).values("member_id", "guide_id").first()
    if not call:
        return None
    if user_id == call["member_id"]:
        return call["guide_id"]
    if user_id == call["guide_id"]:
        return call["member_id"]
    return None


# WhatsApp hand-off and the in-app backup: what either side reports about the video connection.
CHANNEL_NOTES = {
    "opened": "{guide} started the WhatsApp video call.",
    "connected": "Video call connected.",
    "failed": "{who} could not connect the video call.",
    "inapp": "{who} switched to video in Stelleye.",
    "whatsapp": "{who} switched back to WhatsApp.",
}


def channel_update(call_id: int, user_id: int, state: str) -> dict | None:
    """Record a video-connection step from one participant of a running call, as a system note."""
    if state not in CHANNEL_NOTES:
        return None
    if state in ("inapp", "whatsapp") and not settings.SIGHTLINE["inapp_video_test_mode"]:
        return None  # WhatsApp is the only video channel; in-app video is a test-only mode
    call = _call(call_id)
    if user_id not in (call.member_id, call.guide_id) or call.status != S.ACTIVE:
        return None
    if state == "opened" and user_id != call.guide_id:
        return None
    by = "member" if user_id == call.member_id else "guide"
    who = (call.member if by == "member" else call.guide).profile.display_name
    text = CHANNEL_NOTES[state].format(guide=call.guide.profile.display_name, who=who)
    call.video_state = state
    call.save(update_fields=["video_state"])
    return {"state": state, "by": by, "who": who, "note": note(call_id, text),
            "member_id": call.member_id, "guide_id": call.guide_id}


# ---- read helpers ------------------------------------------------------------------


def status_of(call_id: int) -> str | None:
    return CallSession.objects.filter(pk=call_id).values_list("status", flat=True).first()


def live_call_for(user_id: int) -> int | None:
    """A call this user is in that is still ringing, connecting, running or settling."""
    now = timezone.now()
    calls = CallSession.objects.filter(Q(member_id=user_id) | Q(guide_id=user_id), status__in=LIVE)
    for call in calls.only("id", "status", "created_at"):
        limit = STALE_AFTER.get(call.status)
        if limit and now - call.created_at > limit:
            continue
        return call.id
    return None


def call_view(call_id: int, viewer_id: int) -> dict:
    """Everything one side needs to draw (or redraw after a reload) the call stage."""
    call = _call(call_id)
    cfg = get_config()
    is_member = viewer_id == call.member_id
    peer = call.guide if is_member else call.member
    peer_profile = peer.profile if peer else None
    member, guide = call.member.profile, call.guide.profile if call.guide else None
    whatsapp_info = {
        "member_name": member.display_name,
        "member_whatsapp": member.whatsapp,
        "guide_name": guide.display_name if guide else "",
        "guide_whatsapp": guide.whatsapp if guide else "",
    }
    return {
        "call_id": call.pk,
        "state": call.status,
        "role": "member" if is_member else "guide",
        "peer": {
            "id": peer_profile.id if peer_profile else None,
            "name": peer_profile.display_name if peer_profile else "",
            "intro": peer_profile.spoken_intro if peer_profile else "",
            "initials": peer_profile.initials if peer_profile else "?",
            "saved": bool(is_member and peer_profile and member.favorite_guides.filter(pk=peer_profile.pk).exists()),
        },
        "rate": display(call.rate_per_minute) if call.rate_per_minute else None,
        "budget": display(call.budget),
        "max_seconds": max_seconds(call),
        "started_at": call.started_at.timestamp() if call.started_at else None,
        "server_now": timezone.now().timestamp(),
        "open_tx_url": cfg.tx_url(call.open_tx),
        "video_state": call.video_state,
        "whatsapp": (channel.for_member if is_member else channel.for_guide)(whatsapp_info) if guide else None,
        "messages": [message_payload(m, call) for m in call.messages.all()],
    }


def summary(call: CallSession) -> dict:
    cfg = get_config()
    earned = call.earned or Decimal("0")
    return {
        "call_id": call.pk,
        "status": call.status,
        "status_label": call.get_status_display(),
        "seconds": call.seconds,
        "rate": display(call.rate_per_minute or 0),
        "budget": display(call.budget),
        "earned": display(earned),
        "refunded": display(call.refunded or 0),
        "guide_share": display(call.guide_payout if call.guide_payout is not None else guide_share(earned)),
        "hold_until": call.hold_until.isoformat() if call.hold_until else None,
        "open_tx_url": cfg.tx_url(call.open_tx),
        "close_tx_url": cfg.tx_url(call.close_tx),
        "payout_tx_url": cfg.tx_url(call.payout_tx),
        "can_confirm": call.can_confirm,
        "can_report": call.can_report,
        "can_claim": call.can_claim,
    }
