"""Typed client for the CallEscrow contract (contracts/call_escrow)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from stellar_sdk import Keypair, scval

from . import rpc
from .amounts import from_units, to_units
from .config import StellarConfig, get_config


@dataclass(frozen=True)
class OnchainSession:
    session_id: int
    user: str
    guide: str
    rate_per_minute: Decimal
    budget: Decimal
    started_at: datetime | None
    ended_at: datetime | None
    earned: Decimal
    status: str
    recording_hash: str | None


def _address(value) -> str:
    return getattr(value, "address", None) or str(value)


def _timestamp(seconds) -> datetime | None:
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc) if seconds else None


def _enum_name(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode()
    return str(value)


class EscrowClient:
    def __init__(self, cfg: StellarConfig | None = None):
        self.cfg = cfg or get_config()

    @property
    def contract_id(self) -> str:
        return self.cfg.escrow_contract_id

    def _admin(self) -> Keypair:
        return Keypair.from_secret(self.cfg.admin_secret)

    def _invoke(self, signer: Keypair, function: str, *args) -> rpc.TxResult:
        return rpc.invoke(signer, self.contract_id, function, args, self.cfg)

    # ---- guide registry ------------------------------------------------------

    def verify_guide(self, guide_public_key: str) -> str:
        return self._invoke(self._admin(), "verify_guide", scval.to_address(guide_public_key)).hash

    def revoke_guide(self, guide_public_key: str) -> str:
        return self._invoke(self._admin(), "revoke_guide", scval.to_address(guide_public_key)).hash

    def is_verified(self, guide_public_key: str) -> bool:
        return bool(
            rpc.read(self.contract_id, "is_verified", [scval.to_address(guide_public_key)], self.cfg)
        )

    # ---- call lifecycle ------------------------------------------------------

    def open(self, user: Keypair, guide_public_key: str, rate_per_minute: Decimal, budget: Decimal) -> tuple[int, str]:
        result = self._invoke(
            user,
            "open",
            scval.to_address(user.public_key),
            scval.to_address(guide_public_key),
            scval.to_int128(to_units(rate_per_minute)),
            scval.to_int128(to_units(budget)),
        )
        return int(result.return_value), result.hash

    def close(self, caller: Keypair, session_id: int, recording_hash: bytes | None = None) -> tuple[Decimal, str]:
        hash_arg = scval.to_bytes(recording_hash) if recording_hash else scval.to_void()
        result = self._invoke(
            caller, "close", scval.to_address(caller.public_key), scval.to_uint64(session_id), hash_arg
        )
        return from_units(result.return_value), result.hash

    def confirm(self, user: Keypair, session_id: int) -> str:
        return self._invoke(user, "confirm", scval.to_uint64(session_id)).hash

    def report(self, user: Keypair, session_id: int) -> str:
        return self._invoke(user, "report", scval.to_uint64(session_id)).hash

    def resolve(self, session_id: int, user_refund: Decimal) -> str:
        return self._invoke(
            self._admin(), "resolve", scval.to_uint64(session_id), scval.to_int128(to_units(user_refund))
        ).hash

    def claim(self, guide: Keypair, session_id: int) -> tuple[Decimal, str]:
        result = self._invoke(guide, "claim", scval.to_uint64(session_id))
        return from_units(result.return_value), result.hash

    # ---- views ---------------------------------------------------------------

    def get_session(self, session_id: int) -> OnchainSession:
        raw = rpc.read(self.contract_id, "get_session", [scval.to_uint64(session_id)], self.cfg)
        recording = raw.get("recording_hash")
        return OnchainSession(
            session_id=session_id,
            user=_address(raw["user"]),
            guide=_address(raw["guide"]),
            rate_per_minute=from_units(raw["rate_per_minute"]),
            budget=from_units(raw["budget"]),
            started_at=_timestamp(raw["started_at"]),
            ended_at=_timestamp(raw["ended_at"]),
            earned=from_units(raw["earned"]),
            status=_enum_name(raw["status"]),
            recording_hash=recording.hex() if recording else None,
        )

    def hold_seconds(self) -> int:
        return int(rpc.read(self.contract_id, "hold_seconds", [], self.cfg))
