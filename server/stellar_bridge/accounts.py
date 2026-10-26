"""Classic-account plumbing: funding, trustlines, payments and balances."""
import base64
from decimal import Decimal

import requests
from stellar_sdk import Asset, Keypair, Server, TransactionBuilder

from .config import StellarConfig, get_config
from .errors import StellarBridgeError

BASE_FEE = 100


def horizon(cfg: StellarConfig | None = None) -> Server:
    return Server((cfg or get_config()).horizon_url)


def stable_asset(cfg: StellarConfig | None = None) -> Asset:
    cfg = cfg or get_config()
    return Asset(cfg.asset_code, cfg.asset_issuer)


def fund_with_friendbot(public_key: str, cfg: StellarConfig | None = None) -> None:
    cfg = cfg or get_config()
    response = requests.get(cfg.friendbot_url, params={"addr": public_key}, timeout=30)
    if response.status_code != 200 and "createAccountAlreadyExist" not in response.text:
        raise StellarBridgeError(f"Friendbot failed ({response.status_code}): {response.text[:200]}")


def add_trustline(signer: Keypair, cfg: StellarConfig | None = None) -> str:
    cfg = cfg or get_config()
    server = horizon(cfg)
    account = server.load_account(signer.public_key)
    tx = (
        TransactionBuilder(account, cfg.network_passphrase, base_fee=BASE_FEE)
        .append_change_trust_op(stable_asset(cfg))
        .set_timeout(60)
        .build()
    )
    tx.sign(signer)
    return server.submit_transaction(tx)["hash"]


def mint(to_public_key: str, amount: Decimal, cfg: StellarConfig | None = None) -> str:
    """Issue demo stablecoin from the test issuer (stand-in for USDC on testnet)."""
    cfg = cfg or get_config()
    issuer = Keypair.from_secret(cfg.issuer_secret)
    server = horizon(cfg)
    account = server.load_account(issuer.public_key)
    tx = (
        TransactionBuilder(account, cfg.network_passphrase, base_fee=BASE_FEE)
        .append_payment_op(destination=to_public_key, asset=stable_asset(cfg), amount=str(amount))
        .set_timeout(60)
        .build()
    )
    tx.sign(issuer)
    return server.submit_transaction(tx)["hash"]


def has_trustline(public_key: str, cfg: StellarConfig | None = None) -> bool:
    cfg = cfg or get_config()
    account = horizon(cfg).accounts().account_id(public_key).call()
    return any(e.get("asset_code") == cfg.asset_code and e.get("asset_issuer") == cfg.asset_issuer for e in account["balances"])


def ensure_trustline(signer: Keypair, cfg: StellarConfig | None = None) -> None:
    """Let the account hold the configured USDC (accounts made before a token switch lack it)."""
    if not has_trustline(signer.public_key, cfg):
        add_trustline(signer, cfg)


def pay(signer: Keypair, destination: str, amount: str, memo: str = "", memo_type: str = "text",
        cfg: StellarConfig | None = None) -> str:
    """Send the configured USDC, e.g. to an anchor for a withdrawal (the memo identifies the transfer)."""
    cfg = cfg or get_config()
    server = horizon(cfg)
    builder = (
        TransactionBuilder(server.load_account(signer.public_key), cfg.network_passphrase, base_fee=BASE_FEE)
        .append_payment_op(destination=destination, asset=stable_asset(cfg), amount=str(amount))
        .set_timeout(60)
    )
    if memo:
        if memo_type == "id":
            builder.add_id_memo(int(memo))
        elif memo_type == "hash":
            builder.add_hash_memo(base64.b64decode(memo))
        else:
            builder.add_text_memo(memo)
    tx = builder.build()
    tx.sign(signer)
    return server.submit_transaction(tx)["hash"]


def balance(public_key: str, cfg: StellarConfig | None = None) -> Decimal:
    cfg = cfg or get_config()
    account = horizon(cfg).accounts().account_id(public_key).call()
    for entry in account["balances"]:
        if entry.get("asset_code") == cfg.asset_code and entry.get("asset_issuer") == cfg.asset_issuer:
            return Decimal(entry["balance"])
    return Decimal("0")


def provision(starting_balance: Decimal = Decimal("0"), cfg: StellarConfig | None = None) -> Keypair:
    """Create a funded testnet account that can hold the stablecoin."""
    cfg = cfg or get_config()
    keypair = Keypair.random()
    fund_with_friendbot(keypair.public_key, cfg)
    add_trustline(keypair, cfg)
    if starting_balance > 0 and cfg.can_mint:
        mint(keypair.public_key, starting_balance, cfg)
    return keypair
