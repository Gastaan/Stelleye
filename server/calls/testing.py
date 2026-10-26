"""Test doubles shared by the app test suites (no network)."""
import time
from datetime import datetime, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from stellar_sdk import Keypair

from people.models import Profile
from stellar_bridge.escrow import OnchainSession
from wallets.models import Wallet


class FakeEscrow:
    """In-memory stand-in for EscrowClient with the contract's billing rules."""

    sessions: dict = {}
    clock = 0

    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def reset(cls):
        """Start the fake ledger clock at real time so hold windows behave as they would on-chain."""
        cls.sessions = {}
        cls.clock = int(time.time())

    @classmethod
    def advance(cls, seconds: int):
        cls.clock += seconds

    def open(self, user, guide_public_key, rate, budget):
        session_id = len(self.sessions) + 1
        self.sessions[session_id] = {"rate": rate, "budget": budget, "start": self.clock, "end": 0, "earned": Decimal(0)}
        return session_id, f"open{session_id}"

    def close(self, caller, session_id, recording_hash=None):
        s = self.sessions[session_id]
        s["end"] = self.clock
        s["earned"] = min(s["budget"], (s["rate"] * (s["end"] - s["start"]) / 60).quantize(Decimal("0.0000001")))
        return s["earned"], f"close{session_id}"

    def get_session(self, session_id):
        s = self.sessions[session_id]
        to_dt = lambda t: datetime.fromtimestamp(t, tz=timezone.utc) if t else None  # noqa: E731
        return OnchainSession(session_id, "G", "G", s["rate"], s["budget"], to_dt(s["start"]), to_dt(s["end"]),
                              s["earned"], "Held", None)

    def confirm(self, user, session_id):
        return f"confirm{session_id}"

    def claim(self, guide, session_id):
        return self.sessions[session_id]["earned"], f"claim{session_id}"

    def report(self, user, session_id):
        return f"report{session_id}"

    def resolve(self, session_id, refund):
        return f"resolve{session_id}"


def make_person(username, role, languages="en", rate="0.20", verified=True, city="", whatsapp="34600111222") -> Profile:
    user = get_user_model().objects.create_user(username=username, password="x")
    keypair = Keypair.random()
    Wallet.objects.create(owner=user, public_key=keypair.public_key, secret_seed=keypair.secret)
    return Profile.objects.create(user=user, role=role, display_name=username.title(), languages=languages,
                                  rate_per_minute=Decimal(rate), is_verified=verified, city=city, whatsapp=whatsapp)
