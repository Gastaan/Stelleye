"""Live channel per signed-in user: guide directory, matching, chat, video signalling and settlement.

The video itself is a WhatsApp call the guide places (see channel.py); `channel` messages track that
hand-off. In-app WebRTC video is the backup, signalled with `signal` messages.

Client -> server messages: go_online, go_offline, request_help, accept, decline, hangup, chat, channel, signal.
Server -> client messages: hello, guides, presence, status, incoming, cancelled, matched, connected,
                           resume, chat, channel, signal, ending, ended, verified, error.
"""
import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from people.models import Profile
from stellar_bridge.amounts import display
from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError
from wallets import services as wallets

from . import matching, presence, services
from .models import CallSession

log = logging.getLogger(__name__)
S = CallSession.Status
BUDGET_GRACE_SECONDS = 15
LOBBY = "lobby"
MAX_SIGNAL_BYTES = 64_000
MIN_BUDGET, MAX_BUDGET = Decimal("0.50"), Decimal("50")
TAKEN_OVER = 4001  # close code: a newer page of the same person took over

db = database_sync_to_async
# Hang-ups that arrive while the payment lock is still in flight: call id -> who hung up.
_pending_hangups: dict[int, int] = {}


def chain(fn):
    """Run a slow Stellar round-trip in a worker thread without blocking other users."""
    return database_sync_to_async(fn, thread_sensitive=False)


def user_group(user_id: int) -> str:
    return f"user-{user_id}"


def friendly(exc: Exception) -> str:
    return str(exc) if isinstance(exc, StellarBridgeError) else "The Stellar network did not respond. Please try again."


def parse_budget(raw) -> Decimal:
    try:
        budget = Decimal(str(raw or settings.SIGHTLINE["default_budget"]))
    except InvalidOperation:
        budget = Decimal(settings.SIGHTLINE["default_budget"])
    return min(max(budget, MIN_BUDGET), MAX_BUDGET)


def _participants(call_id: int) -> tuple[int, int | None]:
    return tuple(CallSession.objects.values_list("member_id", "guide_id").get(pk=call_id))


def _display_name(user_id: int) -> str:
    return Profile.objects.filter(user_id=user_id).values_list("display_name", flat=True).first() or "Your guide"


class LiveConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        self.group = None
        if not self.user.is_authenticated:
            await self.close()
            return
        self.profile = await db(lambda: getattr(self.user, "profile", None))()
        if self.profile is None:
            await self.close()
            return
        self.group = user_group(self.user.id)
        # Count this page first, so the older page leaving below never looks like the person going away.
        presence.connected(self.user.id)
        # One live page per person: older tabs or devices step aside, so a call never runs twice.
        await self.channel_layer.group_send(self.group, {"type": "session.takeover", "channel": self.channel_name})
        await self.channel_layer.group_add(self.group, self.channel_name)
        if not self.profile.is_guide:
            await self.channel_layer.group_add(LOBBY, self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello", "role": self.profile.role, "name": self.profile.display_name,
                              "user_id": self.user.id, "verified": self.profile.is_verified})
        if not self.profile.is_guide:
            await self._send_guides()
        await self._resume()

    async def disconnect(self, code):
        """A closed page never ends a paid call: the call view comes back when the page reopens.

        Only when the user's last page closes: guides go offline and calls still ringing are cancelled.
        Running calls end when either side presses end, or automatically when the budget runs out.
        """
        if not self.group:
            return
        await self.channel_layer.group_discard(self.group, self.channel_name)
        if not self.profile.is_guide:
            await self.channel_layer.group_discard(LOBBY, self.channel_name)
        if presence.disconnected(self.user.id) > 0:
            return
        if self.profile.is_guide:
            presence.set_offline(self.user.id)
            await self._presence_changed()
        call_id = await db(services.live_call_for)(self.user.id)
        if call_id and await db(services.status_of)(call_id) == S.RINGING:
            if self.profile.is_guide:
                await self._decline(call_id, self.user.id)
            else:
                await self._cancel_ringing(call_id)

    async def receive_json(self, content, **kwargs):
        handler = getattr(self, f"on_{content.get('type', '')}", None)
        if handler is None:
            await self.send_json({"type": "error", "message": "Unknown action."})
            return
        try:
            await handler(content)
        except StellarBridgeError as exc:
            log.exception("stellar error")
            await self.send_json({"type": "error", "message": str(exc)})
        except (KeyError, TypeError, ValueError, CallSession.DoesNotExist):
            await self.send_json({"type": "error", "message": "That action did not match a call."})

    async def deliver(self, event):
        await self.send_json(event["payload"])

    async def session_takeover(self, event):
        if event["channel"] == self.channel_name:
            return
        await self.send_json({"type": "elsewhere", "message": "Stelleye is open in another window or on another device."})
        await self.close(code=TAKEN_OVER)

    async def guides_changed(self, event):
        if not self.profile.is_guide:
            await self._send_guides()

    async def notify(self, user_id: int, payload: dict) -> None:
        await self.channel_layer.group_send(user_group(user_id), {"type": "deliver", "payload": payload})

    async def _presence_changed(self) -> None:
        await self.channel_layer.group_send(LOBBY, {"type": "guides.changed"})

    async def _send_guides(self) -> None:
        guides = await db(matching.guide_directory)(self.user.id)
        await self.send_json({"type": "guides", "guides": guides})

    async def _resume(self) -> None:
        """Redraw a call that is still going after a reload or a trip to another app."""
        call_id = await db(services.live_call_for)(self.user.id)
        if not call_id:
            return
        if await db(services.status_of)(call_id) == S.RINGING:
            if not self.profile.is_guide:
                await self.send_json({"type": "status", "state": "ringing", "call_id": call_id,
                                      "message": "Still calling a guide."})
            return
        view = await db(services.call_view)(call_id, self.user.id)
        await self.send_json({"type": "resume", **view})

    # ---- guide presence -------------------------------------------------------

    async def on_go_online(self, content):
        if not self.profile.is_guide:
            return
        await db(self.profile.refresh_from_db)(fields=["is_verified"])
        if not self.profile.is_verified:
            await self.send_json({"type": "error", "code": "unverified",
                                  "message": "Your account is waiting for verification."})
            return
        if not await self._has_whatsapp():
            await self.send_json({"type": "error", "code": "whatsapp_missing",
                                  "message": "Add your WhatsApp number first, so you can video call the people you help."})
            return
        presence.set_online(self.user.id)
        await self.send_json({"type": "presence", "online": True})
        await self._presence_changed()

    async def on_go_offline(self, content):
        presence.set_offline(self.user.id)
        await self.send_json({"type": "presence", "online": False})
        await self._presence_changed()

    # ---- member asks for help -------------------------------------------------

    async def on_request_help(self, content):
        if self.profile.is_guide:
            return
        if await db(services.live_call_for)(self.user.id):
            await self.send_json({"type": "error", "message": "You already have a call in progress."})
            await self._resume()
            return
        if not await self._has_whatsapp():
            await self.send_json({"type": "error", "code": "whatsapp_missing",
                                  "message": "Add your WhatsApp number first, so your guide can video call you."})
            return
        language = content.get("language") or (self.profile.language_codes or ["en"])[0]
        budget = parse_budget(content.get("budget"))

        chosen = None
        if content.get("guide_id"):
            chosen = await db(matching.available_guide)(int(content["guide_id"]))
            if chosen is None:
                await self.send_json({"type": "error", "code": "guide_unavailable",
                                      "message": "That guide just became unavailable. Choose another guide, "
                                                 "or press Get help now."})
                await self._send_guides()
                return

        try:
            balance = await chain(wallets.balance_of)(self.user)
        except Exception:  # noqa: BLE001 - the on-chain lock still enforces the balance
            log.warning("balance check failed for user %s", self.user.id, exc_info=True)
            balance = None
        if balance is not None and balance < budget:
            asset = get_config().asset_code
            await self.send_json({"type": "error", "code": "low_balance",
                                  "message": f"Your balance is {display(balance)} {asset}, and this call sets aside "
                                             f"{display(budget)} {asset}. Add money in your wallet or choose a smaller amount."})
            return

        call = await db(services.create_call)(self.user.id, language, budget, chosen is not None)
        await self.notify(self.user.id, {"type": "status", "state": "searching", "call_id": call.id,
                                         "message": "Looking for a guide."})
        if chosen:
            await self._ring(call.id, chosen)
        else:
            await self._ring_next(call.id)

    async def _ring_next(self, call_id: int) -> None:
        guide = await db(matching.next_guide_for)(call_id)
        if guide is None:
            member_id, _ = await db(_participants)(call_id)
            await db(services.mark)(call_id, S.MISSED)
            await self.notify(member_id, {"type": "status", "state": "no_guide", "call_id": call_id,
                                          "message": "No guides are available right now. Please try again in a moment."})
            return
        await self._ring(call_id, guide)

    async def _ring(self, call_id: int, guide: Profile) -> None:
        presence.set_busy(guide.user_id, True)
        await self._presence_changed()
        info = await db(services.assign_guide)(call_id, guide.user_id)
        await self.notify(guide.user_id, {"type": "incoming", "call_id": call_id, "member": info["member_name"],
                                          "initials": info["member_initials"], "language": info["language"],
                                          "rate": info["rate"], "budget": info["budget"], "direct": info["direct"]})
        await self.notify(info["member_id"], {"type": "status", "state": "ringing", "call_id": call_id,
                                              "guide": info["guide_name"],
                                              "message": f"Calling {info['guide_intro']}."})
        asyncio.create_task(self._ring_timeout(call_id, guide.user_id))

    async def _ring_timeout(self, call_id: int, guide_id: int) -> None:
        await asyncio.sleep(settings.SIGHTLINE["ring_timeout_seconds"])
        if await db(services.is_ringing)(call_id, guide_id):
            await self.notify(guide_id, {"type": "cancelled", "call_id": call_id, "message": "Missed call."})
            await self._decline(call_id, guide_id)

    async def on_decline(self, content):
        await self._decline(int(content["call_id"]), self.user.id)

    async def _decline(self, call_id: int, guide_id: int) -> None:
        counted = await db(services.record_decline)(call_id, guide_id)
        presence.set_busy(guide_id, False)
        await self._presence_changed()
        if not counted:
            return
        if await db(services.is_direct)(call_id):
            await db(services.mark)(call_id, S.MISSED)
            member_id, _ = await db(_participants)(call_id)
            name = await db(_display_name)(guide_id)
            await self.notify(member_id, {"type": "status", "state": "declined", "call_id": call_id,
                                          "message": f"{name} can't take your call right now. Choose another guide, "
                                                     "or press Get help now for the first available guide."})
        else:
            await self._ring_next(call_id)

    # ---- guide answers -----------------------------------------------------

    async def on_accept(self, content):
        call_id = int(content["call_id"])
        ids = await db(services.begin_connecting)(call_id, self.user.id)
        if ids is None:
            await self.send_json({"type": "error", "message": "This call is no longer available."})
            return
        both = (ids["member_id"], ids["guide_id"])
        for user_id in both:
            view = await db(services.call_view)(call_id, user_id)
            await self.notify(user_id, {"type": "matched", **view})
        try:
            await chain(services.start_call)(call_id)
        except Exception as exc:  # noqa: BLE001 - any failure must release the guide
            log.exception("open failed for call %s", call_id)
            _pending_hangups.pop(call_id, None)
            await db(services.mark)(call_id, S.FAILED)
            presence.set_busy(self.user.id, False)
            await self._presence_changed()
            failed = {"type": "error", "code": "payment_failed", "call_id": call_id,
                      "message": f"Payment could not be secured, so the call was stopped. {friendly(exc)}"}
            for user_id in both:
                await self.notify(user_id, failed)
            return
        view = None
        for user_id in both:
            view = await db(services.call_view)(call_id, user_id)
            await self.notify(user_id, {"type": "connected", **view})
        if call_id in _pending_hangups:
            await self._settle(call_id, ended_by=_pending_hangups.pop(call_id))
            return
        asyncio.create_task(self._end_when_budget_used(call_id, view["max_seconds"]))

    async def _end_when_budget_used(self, call_id: int, max_seconds: int) -> None:
        """Safety net if nobody presses end: settle once the locked budget is fully used."""
        await asyncio.sleep(max_seconds + BUDGET_GRACE_SECONDS)
        if await db(services.status_of)(call_id) == S.ACTIVE:
            await self._settle(call_id, ended_by=None)

    async def _has_whatsapp(self) -> bool:
        await db(self.profile.refresh_from_db)(fields=["whatsapp"])
        return bool(self.profile.whatsapp)

    # ---- chat, WhatsApp hand-off and backup video signalling ----------------------

    async def on_channel(self, content):
        """`opened` (guide tapped the WhatsApp button), `connected`, `failed`, `inapp`, `whatsapp`."""
        update = await db(services.channel_update)(int(content["call_id"]), self.user.id, str(content.get("state")))
        if update is None:
            return
        for user_id in (update["member_id"], update["guide_id"]):
            await self.notify(user_id, {"type": "channel", "call_id": int(content["call_id"]), "state": update["state"],
                                        "by": update["by"], "who": update["who"], "note": update["note"]})

    async def on_chat(self, content):
        call_id = int(content["call_id"])
        message = await db(services.post_message)(call_id, self.user.id, content.get("text", ""))
        if message is None:
            await self.send_json({"type": "error", "code": "chat_closed",
                                  "message": "Messages can be sent only while the call is open."})
            return
        for user_id in await db(_participants)(call_id):
            await self.notify(user_id, {"type": "chat", **message})

    async def on_signal(self, content):
        """Relay WebRTC offers, answers and ICE candidates to the other participant (in-app video test mode only)."""
        if not settings.SIGHTLINE["inapp_video_test_mode"]:
            return
        call_id = int(content["call_id"])
        data = content.get("data")
        if not isinstance(data, dict) or len(json.dumps(data)) > MAX_SIGNAL_BYTES:
            return
        peer = await db(services.signal_peer)(call_id, self.user.id)
        if peer:
            await self.notify(peer, {"type": "signal", "call_id": call_id, "data": data})

    # ---- hang up ------------------------------------------------------------

    async def on_hangup(self, content):
        await self._hang_up(int(content["call_id"]))

    async def _hang_up(self, call_id: int) -> None:
        member_id, guide_id = await db(_participants)(call_id)
        if self.user.id not in (member_id, guide_id):
            return
        status = await db(services.status_of)(call_id)
        if status == S.RINGING and self.user.id == member_id:
            await self._cancel_ringing(call_id)
        elif status == S.CONNECTING:
            _pending_hangups[call_id] = self.user.id
            await self.send_json({"type": "status", "state": "securing", "call_id": call_id,
                                  "message": "Ending the call as soon as the payment lock finishes. "
                                             "You will only pay for the seconds used."})
        elif status == S.ACTIVE:
            await self._settle(call_id, ended_by=self.user.id)

    async def _cancel_ringing(self, call_id: int) -> None:
        guide_id = await db(services.cancel_ringing)(call_id, self.user.id)
        if guide_id:
            presence.set_busy(guide_id, False)
            await self._presence_changed()
            await self.notify(guide_id, {"type": "cancelled", "call_id": call_id, "message": "The caller hung up."})
        await self.notify(self.user.id, {"type": "status", "state": "idle", "call_id": call_id,
                                         "message": "Call cancelled."})

    async def _settle(self, call_id: int, ended_by: int | None) -> None:
        """Close on-chain. `ended_by=None` means the budget ran out; the member's key signs."""
        participants = await db(services.begin_ending)(call_id)
        if participants is None:
            return
        member_id, guide_id = participants
        ending = {"type": "ending", "call_id": call_id, "message": "Call ended. Settling payment on Stellar."}
        await self.notify(member_id, ending)
        await self.notify(guide_id, ending)
        try:
            result = await chain(services.end_call)(call_id, ended_by or member_id)
        except Exception as exc:  # noqa: BLE001 - keep the call open so it can be retried
            log.exception("close failed for call %s", call_id)
            await db(services.mark)(call_id, S.ACTIVE)
            failure = {"type": "error", "code": "settle_failed", "call_id": call_id,
                       "message": f"Could not settle the call yet. Press end again. {friendly(exc)}"}
            await self.notify(member_id, failure)
            await self.notify(guide_id, failure)
            return
        for user_id, audience in ((member_id, "member"), (guide_id, "guide")):
            view = await db(services.call_view)(call_id, user_id)
            await self.notify(user_id, {"type": "ended", "audience": audience, **result,
                                        "peer": view["peer"], "messages": view["messages"]})
        presence.set_busy(guide_id, False)
        await self._presence_changed()
