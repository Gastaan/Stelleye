"""Real money in and out through a Stellar anchor: SEP-1 discovery, SEP-10 sign-in, SEP-24 hosted transfers.

On testnet this talks to testanchor.stellar.org, which pays out Circle's testnet USDC; in production the
same code points at a licensed anchor (MoneyGram Ramps, a bank, a local exchange) by changing
ANCHOR_HOME_DOMAIN. The anchor hosts the card, bank and identity steps, so Stelleye never handles them.
"""
import time
from dataclasses import dataclass
from functools import lru_cache

import requests
from stellar_sdk import Keypair
from stellar_sdk.exceptions import BaseRequestError
from stellar_sdk.sep.exceptions import StellarTomlNotFoundError, InvalidSep10ChallengeError
from stellar_sdk.sep.stellar_toml import fetch_stellar_toml
from stellar_sdk.sep.stellar_web_authentication import read_challenge_transaction

from .config import StellarConfig, get_config
from .errors import StellarBridgeError

TIMEOUT = 30
FINAL = {"completed", "refunded", "expired", "error", "no_market", "too_small", "too_large"}


@dataclass(frozen=True)
class AnchorTransaction:
    id: str
    kind: str
    status: str
    amount_in: str | None
    amount_out: str | None
    amount_fee: str | None
    message: str
    more_info_url: str
    stellar_transaction_id: str
    withdraw_anchor_account: str
    withdraw_memo: str
    withdraw_memo_type: str

    @classmethod
    def from_json(cls, data: dict) -> "AnchorTransaction":
        return cls(
            id=data["id"],
            kind=data.get("kind", ""),
            status=data.get("status", ""),
            amount_in=data.get("amount_in"),
            amount_out=data.get("amount_out"),
            amount_fee=data.get("amount_fee") or (data.get("fee_details") or {}).get("total"),
            message=data.get("message") or "",
            more_info_url=data.get("more_info_url") or "",
            stellar_transaction_id=data.get("stellar_transaction_id") or "",
            withdraw_anchor_account=data.get("withdraw_anchor_account") or "",
            withdraw_memo=data.get("withdraw_memo") or "",
            withdraw_memo_type=data.get("withdraw_memo_type") or "",
        )

    @property
    def done(self) -> bool:
        return self.status in FINAL


@lru_cache(maxsize=4)
def _toml(home_domain: str) -> dict:
    try:
        return fetch_stellar_toml(home_domain)
    except (StellarTomlNotFoundError, BaseRequestError, requests.RequestException) as exc:
        raise StellarBridgeError(f"The payment partner ({home_domain}) is not reachable right now.") from exc


# SEP-10 tokens last a while; reuse them per account instead of signing a challenge for every request.
_tokens: dict[tuple[str, str], tuple[str, float]] = {}


class AnchorClient:
    def __init__(self, cfg: StellarConfig | None = None):
        self.cfg = cfg or get_config()
        self.home = self.cfg.anchor_domain

    @property
    def toml(self) -> dict:
        return _toml(self.home)

    @property
    def transfer_server(self) -> str:
        server = self.toml.get("TRANSFER_SERVER_SEP0024")
        if not server:
            raise StellarBridgeError(f"{self.home} does not offer hosted deposits and withdrawals.")
        return server.rstrip("/")

    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise StellarBridgeError("The payment partner did not respond. Please try again.") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise StellarBridgeError(data.get("error") or f"The payment partner refused the request ({response.status_code}).")
        return data

    def token(self, keypair: Keypair) -> str:
        """SEP-10: prove we control the account by signing the anchor's challenge."""
        key = (self.home, keypair.public_key)
        cached = _tokens.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        endpoint = self.toml["WEB_AUTH_ENDPOINT"]
        challenge = self._request("GET", endpoint, params={"account": keypair.public_key, "home_domain": self.home})
        try:
            parsed = read_challenge_transaction(
                challenge["transaction"], self.toml["SIGNING_KEY"], self.home, self.home,
                challenge.get("network_passphrase") or self.cfg.network_passphrase,
            )
        except (InvalidSep10ChallengeError, KeyError) as exc:
            raise StellarBridgeError("The payment partner's sign-in challenge did not check out.") from exc
        envelope = parsed.transaction
        envelope.sign(keypair)
        token = self._request("POST", endpoint, json={"transaction": envelope.to_xdr()})["token"]
        _tokens[key] = (token, time.time() + 15 * 60)
        return token

    def limits(self, kind: str, asset_code: str) -> dict:
        info = self._request("GET", f"{self.transfer_server}/info")
        return info.get(kind, {}).get(asset_code, {})

    def start(self, keypair: Keypair, kind: str, amount: str, lang: str = "en") -> tuple[str, str]:
        """Open a hosted deposit or withdrawal. Returns (anchor transaction id, URL of the anchor's page)."""
        assert kind in ("deposit", "withdraw")
        data = self._request(
            "POST", f"{self.transfer_server}/transactions/{kind}/interactive",
            headers={"Authorization": f"Bearer {self.token(keypair)}"},
            json={"asset_code": self.cfg.asset_code, "asset_issuer": self.cfg.asset_issuer,
                  "account": keypair.public_key, "amount": amount, "lang": lang},
        )
        if data.get("type") != "interactive_customer_info_needed":
            raise StellarBridgeError("The payment partner did not open a transfer page.")
        return data["id"], data["url"]

    def transaction(self, keypair: Keypair, anchor_id: str) -> AnchorTransaction:
        data = self._request(
            "GET", f"{self.transfer_server}/transaction",
            headers={"Authorization": f"Bearer {self.token(keypair)}"}, params={"id": anchor_id},
        )
        return AnchorTransaction.from_json(data["transaction"])
