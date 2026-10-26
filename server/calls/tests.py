from decimal import Decimal
from unittest import mock

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from people.models import Profile

from . import matching, presence, services
from .consumers import LiveConsumer
from .models import CallSession, ChatMessage
from .testing import FakeEscrow, make_person

S = CallSession.Status
GUIDE, MEMBER = Profile.Role.GUIDE, Profile.Role.MEMBER


def reset_presence():
    presence.reset()


class MatchingTests(TestCase):
    def setUp(self):
        reset_presence()
        self.member = make_person("maria", MEMBER, languages="es,en")
        self.cheap = make_person("kofi", GUIDE, languages="en", rate="0.15")
        self.fav = make_person("ana", GUIDE, languages="es,en", rate="0.20")
        self.member.favorite_guides.add(self.fav)
        for guide in (self.cheap, self.fav):
            presence.set_online(guide.user_id)

    def test_prefers_saved_guide_over_cheaper_one(self):
        call = services.create_call(self.member.user_id, "en", Decimal("3"))
        self.assertEqual(matching.next_guide_for(call.id), self.fav)

    def test_filters_by_language(self):
        call = services.create_call(self.member.user_id, "es", Decimal("3"))
        presence.set_offline(self.fav.user_id)
        self.assertIsNone(matching.next_guide_for(call.id))

    def test_skips_guides_who_declined_or_are_busy(self):
        call = services.create_call(self.member.user_id, "en", Decimal("3"))
        call.declined_by.add(self.fav.user)
        self.assertEqual(matching.next_guide_for(call.id), self.cheap)
        presence.set_busy(self.cheap.user_id, True)
        self.assertIsNone(matching.next_guide_for(call.id))

    def test_ignores_unverified_guides(self):
        self.fav.is_verified = False
        self.fav.save()
        call = services.create_call(self.member.user_id, "es", Decimal("3"))
        self.assertIsNone(matching.next_guide_for(call.id))


@mock.patch("calls.services.EscrowClient", FakeEscrow)
class LifecycleTests(TestCase):
    def setUp(self):
        FakeEscrow.reset()
        self.member = make_person("maria", MEMBER)
        self.guide = make_person("ana", GUIDE, rate="0.30")

    def _active_call(self):
        call = services.create_call(self.member.user_id, "en", Decimal("3"))
        services.assign_guide(call.id, self.guide.user_id)
        services.begin_connecting(call.id, self.guide.user_id)
        services.start_call(call.id)
        return call.id

    def test_bills_used_minutes_and_refunds_the_rest(self):
        call_id = self._active_call()
        FakeEscrow.advance(120)
        services.begin_ending(call_id)
        result = services.end_call(call_id, self.member.user_id)
        self.assertEqual(result["earned"], "0.60")
        self.assertEqual(result["refunded"], "2.40")
        self.assertEqual(result["seconds"], 120)
        self.assertTrue(result["can_confirm"])
        self.assertEqual(CallSession.objects.get(pk=call_id).status, S.HELD)

    def test_confirm_pays_guide_minus_fee(self):
        call_id = self._active_call()
        FakeEscrow.advance(60)
        services.begin_ending(call_id)
        services.end_call(call_id, self.guide.user_id)
        result = services.confirm_call(call_id, self.member.user_id)
        self.assertEqual(result["status"], S.RELEASED)
        self.assertEqual(Decimal(result["guide_share"]), services.guide_share(Decimal("0.30")).quantize(Decimal("0.01")))

    def test_only_the_member_can_confirm(self):
        call_id = self._active_call()
        FakeEscrow.advance(60)
        services.begin_ending(call_id)
        services.end_call(call_id, self.member.user_id)
        with self.assertRaises(PermissionError):
            services.confirm_call(call_id, self.guide.user_id)

    def test_instant_hangup_is_free(self):
        call_id = self._active_call()
        services.begin_ending(call_id)
        result = services.end_call(call_id, self.member.user_id)
        self.assertEqual(result["status"], S.RELEASED)
        self.assertEqual(result["earned"], "0.00")

    def test_decline_moves_on_and_cancel_marks_missed(self):
        call = services.create_call(self.member.user_id, "en", Decimal("3"))
        services.assign_guide(call.id, self.guide.user_id)
        self.assertTrue(services.record_decline(call.id, self.guide.user_id))
        services.assign_guide(call.id, self.guide.user_id)
        self.assertEqual(services.cancel_ringing(call.id, self.member.user_id), self.guide.user_id)
        self.assertEqual(services.status_of(call.id), S.MISSED)



class DirectoryTests(TestCase):
    def setUp(self):
        reset_presence()
        self.member = make_person("maria", MEMBER, languages="es,en")
        self.kofi = make_person("kofi", GUIDE, languages="en,fr", rate="0.15", city="Accra")
        self.ana = make_person("ana", GUIDE, languages="es,en", rate="0.20")
        self.lena = make_person("lena", GUIDE, rate="0.10")
        self.pending = make_person("pat", GUIDE, verified=False)

    def test_lists_online_guides_available_first_then_saved_then_cheapest(self):
        for guide in (self.kofi, self.ana, self.lena, self.pending):
            presence.set_online(guide.user_id)
        presence.set_busy(self.lena.user_id, True)
        self.member.favorite_guides.add(self.ana)
        cards = matching.guide_directory(self.member.user_id)
        self.assertEqual([c["name"] for c in cards], ["Ana", "Kofi", "Lena"])
        self.assertEqual([c["state"] for c in cards], ["available", "available", "busy"])
        self.assertTrue(cards[0]["saved"])
        self.assertEqual(cards[1]["language_names"], ["English", "French"])

    def test_saved_guides_are_listed_even_when_offline(self):
        self.member.favorite_guides.add(self.ana)
        cards = matching.guide_directory(self.member.user_id)
        self.assertEqual([(c["name"], c["state"]) for c in cards], [("Ana", "offline")])

    def test_available_guide_requires_online_and_free(self):
        self.assertIsNone(matching.available_guide(self.ana.id))
        presence.set_online(self.ana.user_id)
        self.assertEqual(matching.available_guide(self.ana.id), self.ana)
        presence.set_busy(self.ana.user_id, True)
        self.assertIsNone(matching.available_guide(self.ana.id))
        presence.set_online(self.pending.user_id)
        self.assertIsNone(matching.available_guide(self.pending.id))


@mock.patch("calls.services.EscrowClient", FakeEscrow)
class ChatAndSignalTests(TestCase):
    def setUp(self):
        FakeEscrow.reset()
        self.member = make_person("maria", MEMBER, whatsapp="")
        self.guide = make_person("ana", GUIDE)
        self.stranger = make_person("eve", MEMBER)
        call = services.create_call(self.member.user_id, "en", Decimal("3"))
        services.assign_guide(call.id, self.guide.user_id)
        self.call_id = call.id

    def test_chat_opens_when_the_guide_accepts_and_closes_after_the_call(self):
        self.assertIsNone(services.post_message(self.call_id, self.member.user_id, "hello?"))
        services.begin_connecting(self.call_id, self.guide.user_id)
        message = services.post_message(self.call_id, self.member.user_id, "  Can you   read this label? ")
        self.assertEqual((message["role"], message["text"]), ("member", "Can you read this label?"))
        self.assertIsNone(services.post_message(self.call_id, self.stranger.user_id, "hi"))
        self.assertIsNone(services.post_message(self.call_id, self.guide.user_id, "   "))
        services.start_call(self.call_id)
        services.begin_ending(self.call_id)
        services.end_call(self.call_id, self.member.user_id)
        self.assertIsNone(services.post_message(self.call_id, self.guide.user_id, "bye"))
        roles = [m["role"] for m in services.transcript(self.call_id)]
        # accepted note, member line, payment-locked note, settled note
        self.assertEqual(roles, ["system", "member", "system", "system"])
        self.assertTrue(services.transcript(self.call_id)[2]["link"])

    def test_signalling_only_between_the_two_participants_of_a_live_call(self):
        self.assertIsNone(services.signal_peer(self.call_id, self.member.user_id))  # still ringing
        services.begin_connecting(self.call_id, self.guide.user_id)
        self.assertEqual(services.signal_peer(self.call_id, self.member.user_id), self.guide.user_id)
        self.assertEqual(services.signal_peer(self.call_id, self.guide.user_id), self.member.user_id)
        self.assertIsNone(services.signal_peer(self.call_id, self.stranger.user_id))

    def test_call_view_offers_whatsapp_only_when_both_have_numbers(self):
        services.begin_connecting(self.call_id, self.guide.user_id)
        self.assertIsNone(services.call_view(self.call_id, self.member.user_id)["whatsapp"])
        self.member.whatsapp = "34600111222"
        self.member.save()
        view = services.call_view(self.call_id, self.guide.user_id)
        self.assertEqual(view["whatsapp"]["peer_number"], "+34600111222")
        self.assertEqual(view["role"], "guide")
        self.assertEqual(view["peer"]["name"], "Maria")

    def test_live_call_skips_calls_left_ringing_by_a_restart(self):
        self.assertEqual(services.live_call_for(self.member.user_id), self.call_id)
        CallSession.objects.filter(pk=self.call_id).update(created_at=timezone.now() - timezone.timedelta(minutes=5))
        self.assertIsNone(services.live_call_for(self.member.user_id))


@mock.patch("calls.consumers.wallets.balance_of", return_value=Decimal("25"))
@mock.patch("calls.services.EscrowClient", FakeEscrow)
@mock.patch.object(LiveConsumer, "_end_when_budget_used", new=mock.AsyncMock())
@mock.patch.object(LiveConsumer, "_ring_timeout", new=mock.AsyncMock())
class LiveChannelTests(TransactionTestCase):
    """The whole call over the WebSocket, as the two browsers see it."""

    def setUp(self):
        FakeEscrow.reset()
        reset_presence()

    async def _connect(self, profile):
        communicator = WebsocketCommunicator(LiveConsumer.as_asgi(), "/ws/live/")
        communicator.scope["user"] = profile.user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        hello = await communicator.receive_json_from()
        self.assertEqual(hello["type"], "hello")
        return communicator

    async def _next(self, communicator, wanted, **match):
        while True:
            message = await communicator.receive_json_from(timeout=5)
            if message["type"] == wanted and all(message.get(k) == v for k, v in match.items()):
                return message

    async def _people(self):
        member = await database_sync_to_async(make_person)("maria", MEMBER, whatsapp="34600111222")
        guide = await database_sync_to_async(make_person)("ana", GUIDE, city="Mexico City", whatsapp="525511112222")
        return member, guide

    async def test_full_call_with_chat_video_signalling_and_resume(self, _balance):
        member, guide = await self._people()
        guide_ws = await self._connect(guide)
        member_ws = await self._connect(member)
        self.assertEqual((await self._next(member_ws, "guides"))["guides"], [])

        await guide_ws.send_json_to({"type": "go_online"})
        self.assertTrue((await self._next(guide_ws, "presence"))["online"])
        listed = await self._next(member_ws, "guides")
        self.assertEqual([(g["name"], g["state"]) for g in listed["guides"]], [("Ana", "available")])

        await member_ws.send_json_to({"type": "request_help", "language": "en", "budget": "3.00"})
        ringing = await self._next(member_ws, "status", state="ringing")
        self.assertIn("Ana in Mexico City", ringing["message"])
        incoming = await self._next(guide_ws, "incoming")
        call_id = incoming["call_id"]
        self.assertEqual(incoming["budget"], "3.00")

        await guide_ws.send_json_to({"type": "accept", "call_id": call_id})
        matched = await self._next(member_ws, "matched")
        self.assertEqual(matched["peer"]["name"], "Ana")
        self.assertEqual(matched["messages"][0]["text"], "Ana accepted the call.")
        member_live = await self._next(member_ws, "connected")
        guide_live = await self._next(guide_ws, "connected")
        self.assertEqual((member_live["role"], guide_live["role"]), ("member", "guide"))
        self.assertIsNotNone(member_live["started_at"])
        self.assertEqual(member_live["max_seconds"], 900)  # 3.00 at 0.20 a minute
        self.assertTrue(member_live["whatsapp"]["link"].startswith("https://wa.me/525511112222?text="))
        self.assertTrue(guide_live["whatsapp"]["link"].startswith("https://wa.me/34600111222?text="))

        # Chat reaches both sides, tagged with who sent it.
        await member_ws.send_json_to({"type": "chat", "call_id": call_id, "text": "Is this the 27 bus?"})
        for ws in (member_ws, guide_ws):
            line = await self._next(ws, "chat")
            self.assertEqual((line["role"], line["text"]), ("member", "Is this the 27 bus?"))

        # The guide opens WhatsApp: the member is told to answer, and the step is in the chat.
        await guide_ws.send_json_to({"type": "channel", "call_id": call_id, "state": "opened"})
        opened = await self._next(member_ws, "channel")
        self.assertEqual((opened["state"], opened["by"]), ("opened", "guide"))
        self.assertEqual(opened["note"]["text"], "Ana started the WhatsApp video call.")
        await guide_ws.send_json_to({"type": "channel", "call_id": call_id, "state": "connected"})
        self.assertEqual((await self._next(member_ws, "channel"))["state"], "connected")
        # Only the guide can say they opened WhatsApp.
        await member_ws.send_json_to({"type": "channel", "call_id": call_id, "state": "opened"})

        # WhatsApp is the only video channel: in-app signalling and switching are ignored outside test mode.
        await guide_ws.send_json_to({"type": "signal", "call_id": call_id, "data": {"kind": "hello"}})
        await member_ws.send_json_to({"type": "channel", "call_id": call_id, "state": "inapp"})
        self.assertTrue(await member_ws.receive_nothing(timeout=0.3))

        # The member's page reloads mid-call: the paid call keeps running and the stage comes back.
        await member_ws.disconnect()
        self.assertEqual(await database_sync_to_async(services.status_of)(call_id), S.ACTIVE)
        member_ws = await self._connect(member)
        resumed = await self._next(member_ws, "resume")
        self.assertEqual((resumed["state"], resumed["peer"]["name"]), (S.ACTIVE, "Ana"))
        self.assertEqual(resumed["video_state"], "connected")
        self.assertIn("Is this the 27 bus?", [m["text"] for m in resumed["messages"]])

        FakeEscrow.advance(90)
        await member_ws.send_json_to({"type": "hangup", "call_id": call_id})
        member_ended = await self._next(member_ws, "ended")
        guide_ended = await self._next(guide_ws, "ended")
        self.assertEqual(member_ended["earned"], "0.30")
        self.assertEqual(member_ended["peer"]["name"], "Ana")
        self.assertFalse(member_ended["peer"]["saved"])
        self.assertEqual(guide_ended["audience"], "guide")
        self.assertTrue(member_ended["messages"][-1]["text"].startswith("Call settled on Stellar"))
        # The guide is free again, and members see it.
        refreshed = await self._next(member_ws, "guides")
        self.assertEqual(refreshed["guides"][0]["state"], "available")
        # v1 assumes the call helped: the guide is paid straight away, no hold to release by hand.
        paid = await self._next(member_ws, "paid_out")
        self.assertRegex(paid["payout_tx_url"], r"/confirm\d+$")  # released with the caller's key
        self.assertEqual((await self._next(guide_ws, "paid_out"))["guide_share"], paid["guide_share"])
        self.assertEqual(await database_sync_to_async(services.status_of)(call_id), S.RELEASED)

        await member_ws.disconnect()
        await guide_ws.disconnect()

    @override_settings(SIGHTLINE={**settings.SIGHTLINE, "inapp_video_test_mode": True})
    async def test_inapp_video_works_only_in_test_mode(self, _balance):
        member, guide = await self._people()

        def active_call():
            call = services.create_call(member.user_id, "en", Decimal("3"))
            services.assign_guide(call.id, guide.user_id)
            services.begin_connecting(call.id, guide.user_id)
            services.start_call(call.id)
            return call.id

        call_id = await database_sync_to_async(active_call)()
        guide_ws = await self._connect(guide)
        await self._next(guide_ws, "resume")
        member_ws = await self._connect(member)
        await self._next(member_ws, "resume")
        await member_ws.send_json_to({"type": "channel", "call_id": call_id, "state": "inapp"})
        self.assertEqual((await self._next(guide_ws, "channel"))["state"], "inapp")
        await guide_ws.send_json_to({"type": "signal", "call_id": call_id, "data": {"kind": "hello"}})
        self.assertEqual((await self._next(member_ws, "signal"))["data"], {"kind": "hello"})
        await member_ws.disconnect()
        await guide_ws.disconnect()

    async def test_choosing_a_guide_who_declines_does_not_ring_anyone_else(self, _balance):
        member, ana = await self._people()
        kofi = await database_sync_to_async(make_person)("kofi", GUIDE)
        ana_ws, kofi_ws = await self._connect(ana), await self._connect(kofi)
        for ws in (ana_ws, kofi_ws):
            await ws.send_json_to({"type": "go_online"})
            await self._next(ws, "presence")
        member_ws = await self._connect(member)

        await member_ws.send_json_to({"type": "request_help", "guide_id": ana.id})
        incoming = await self._next(ana_ws, "incoming")
        self.assertTrue(incoming["direct"])
        await ana_ws.send_json_to({"type": "decline", "call_id": incoming["call_id"]})
        declined = await self._next(member_ws, "status", state="declined")
        self.assertIn("Ana can't take your call", declined["message"])
        self.assertEqual(await database_sync_to_async(services.status_of)(incoming["call_id"]), S.MISSED)
        self.assertTrue(await kofi_ws.receive_nothing(timeout=0.3))

        for ws in (member_ws, ana_ws, kofi_ws):
            await ws.disconnect()

    async def test_choosing_a_guide_who_is_offline_is_refused(self, _balance):
        member, ana = await self._people()
        member_ws = await self._connect(member)
        await member_ws.send_json_to({"type": "request_help", "guide_id": ana.id})
        error = await self._next(member_ws, "error")
        self.assertEqual(error["code"], "guide_unavailable")
        await member_ws.disconnect()

    async def test_low_balance_is_caught_before_any_guide_rings(self, balance):
        balance.return_value = Decimal("1.00")
        member, guide = await self._people()
        guide_ws = await self._connect(guide)
        await guide_ws.send_json_to({"type": "go_online"})
        await self._next(guide_ws, "presence")
        member_ws = await self._connect(member)
        await member_ws.send_json_to({"type": "request_help", "budget": "3.00"})
        error = await self._next(member_ws, "error")
        self.assertEqual(error["code"], "low_balance")
        self.assertIn("1.00", error["message"])
        self.assertTrue(await guide_ws.receive_nothing(timeout=0.3))
        await member_ws.disconnect()
        await guide_ws.disconnect()

    async def test_unverified_guides_cannot_go_online(self, _balance):
        guide = await database_sync_to_async(make_person)("pat", GUIDE, verified=False)
        guide_ws = await self._connect(guide)
        await guide_ws.send_json_to({"type": "go_online"})
        error = await self._next(guide_ws, "error")
        self.assertEqual(error["code"], "unverified")
        self.assertFalse(presence.is_online(guide.user_id))
        await guide_ws.disconnect()

    async def test_whatsapp_number_needed_to_ask_for_help_or_go_online(self, _balance):
        member = await database_sync_to_async(make_person)("maria", MEMBER, whatsapp="")
        guide = await database_sync_to_async(make_person)("ana", GUIDE, whatsapp="")
        member_ws, guide_ws = await self._connect(member), await self._connect(guide)
        await member_ws.send_json_to({"type": "request_help"})
        self.assertEqual((await self._next(member_ws, "error"))["code"], "whatsapp_missing")
        await guide_ws.send_json_to({"type": "go_online"})
        self.assertEqual((await self._next(guide_ws, "error"))["code"], "whatsapp_missing")
        self.assertFalse(presence.is_online(guide.user_id))
        await member_ws.disconnect()
        await guide_ws.disconnect()

    async def test_a_new_page_takes_over_and_a_guide_stays_online(self, _balance):
        _, guide = await self._people()
        first = await self._connect(guide)
        await first.send_json_to({"type": "go_online"})
        await self._next(first, "presence")
        second = await self._connect(guide)
        self.assertEqual((await self._next(first, "elsewhere"))["type"], "elsewhere")
        self.assertEqual(await first.receive_output(timeout=2), {"type": "websocket.close", "code": 4001})
        await first.disconnect()
        self.assertTrue(presence.is_online(guide.user_id))
        self.assertTrue(await second.receive_nothing(timeout=0.3))
        await second.disconnect()
        self.assertFalse(presence.is_online(guide.user_id))

    async def test_chat_outside_a_call_is_refused(self, _balance):
        member, guide = await self._people()
        call = await database_sync_to_async(services.create_call)(member.user_id, "en", Decimal("3"))
        member_ws = await self._connect(member)
        await member_ws.send_json_to({"type": "chat", "call_id": call.id, "text": "hi"})
        self.assertEqual((await self._next(member_ws, "error"))["code"], "chat_closed")
        self.assertFalse(await database_sync_to_async(ChatMessage.objects.exists)())
        await member_ws.disconnect()
