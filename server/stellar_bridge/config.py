from dataclasses import dataclass, replace

from django.conf import settings


@dataclass(frozen=True)
class StellarConfig:
    rpc_url: str
    horizon_url: str
    network_passphrase: str
    friendbot_url: str
    explorer_url: str
    escrow_contract_id: str
    token_contract_id: str
    asset_code: str
    asset_issuer: str
    issuer_secret: str
    admin_secret: str
    hold_seconds: int
    fee_bps: int
    anchor_domain: str = "testanchor.stellar.org"

    @property
    def can_mint(self) -> bool:
        """Only a self-issued demo asset can be minted; real USDC arrives through the anchor."""
        return bool(self.issuer_secret)

    @property
    def is_deployed(self) -> bool:
        return bool(self.escrow_contract_id and self.token_contract_id and self.admin_secret)

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.explorer_url}/tx/{tx_hash}" if tx_hash else ""

    def account_url(self, address: str) -> str:
        if not address:
            return ""
        kind = "contract" if address.startswith("C") else "account"
        return f"{self.explorer_url}/{kind}/{address}"

    def with_overrides(self, **changes) -> "StellarConfig":
        return replace(self, **changes)


def get_config() -> StellarConfig:
    return StellarConfig(**settings.STELLAR)
