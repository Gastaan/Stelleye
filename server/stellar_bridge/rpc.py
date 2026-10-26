"""Low-level Soroban helpers: build, simulate, sign, submit and decode."""
import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

from stellar_sdk import Keypair, SorobanServer, TransactionBuilder, scval, xdr
from stellar_sdk.exceptions import PrepareTransactionException
from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

from .config import StellarConfig, get_config
from .errors import ContractError, StellarBridgeError

log = logging.getLogger(__name__)

BASE_FEE = 100
TX_TIMEOUT_SECONDS = 60
CONFIRM_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class TxResult:
    hash: str
    return_value: Any


def soroban(cfg: StellarConfig | None = None) -> SorobanServer:
    return SorobanServer((cfg or get_config()).rpc_url)


def invoke(
    signer: Keypair,
    contract_id: str,
    function: str,
    args: Sequence[xdr.SCVal],
    cfg: StellarConfig | None = None,
) -> TxResult:
    """Invoke a contract function with `signer` as source account (and authorizer)."""
    cfg = cfg or get_config()
    server = soroban(cfg)
    account = server.load_account(signer.public_key)
    tx = (
        TransactionBuilder(account, cfg.network_passphrase, base_fee=BASE_FEE)
        .append_invoke_contract_function_op(contract_id, function, list(args))
        .set_timeout(TX_TIMEOUT_SECONDS)
        .build()
    )
    return submit(tx, signer, cfg)


def submit(tx, signer: Keypair, cfg: StellarConfig | None = None) -> TxResult:
    """Simulate (fills footprint, auth and fees), sign, send and wait for the result."""
    cfg = cfg or get_config()
    server = soroban(cfg)
    try:
        tx = server.prepare_transaction(tx)
    except PrepareTransactionException as exc:
        detail = getattr(exc.simulate_transaction_response, "error", "") or str(exc)
        raise ContractError.from_text(detail) from exc
    tx.sign(signer)

    sent = server.send_transaction(tx)
    if sent.status == SendTransactionStatus.ERROR:
        raise StellarBridgeError(f"Transaction was not accepted: {sent.error_result_xdr}")

    deadline = time.monotonic() + CONFIRM_TIMEOUT_SECONDS
    while True:
        result = server.get_transaction(sent.hash)
        if result.status != GetTransactionStatus.NOT_FOUND:
            break
        if time.monotonic() > deadline:
            raise StellarBridgeError(f"Timed out waiting for transaction {sent.hash}")
        time.sleep(1)

    if result.status != GetTransactionStatus.SUCCESS:
        raise ContractError.from_text(result.result_xdr or "transaction failed")
    log.info("stellar tx %s ok", sent.hash)
    return TxResult(hash=sent.hash, return_value=_return_value(result.result_meta_xdr))


def read(contract_id: str, function: str, args: Sequence[xdr.SCVal], cfg: StellarConfig | None = None) -> Any:
    """Read-only call through simulation; nothing is submitted."""
    cfg = cfg or get_config()
    server = soroban(cfg)
    source = server.load_account(Keypair.from_secret(cfg.admin_secret).public_key)
    tx = (
        TransactionBuilder(source, cfg.network_passphrase, base_fee=BASE_FEE)
        .append_invoke_contract_function_op(contract_id, function, list(args))
        .set_timeout(TX_TIMEOUT_SECONDS)
        .build()
    )
    sim = server.simulate_transaction(tx)
    if sim.error:
        raise ContractError.from_text(sim.error)
    return scval.to_native(xdr.SCVal.from_xdr(sim.results[0].xdr))


def _return_value(meta_xdr: str | None) -> Any:
    if not meta_xdr:
        return None
    meta = xdr.TransactionMeta.from_xdr(meta_xdr)
    for version in ("v4", "v3"):
        body = getattr(meta, version, None)
        soroban_meta = getattr(body, "soroban_meta", None) if body is not None else None
        if soroban_meta is not None and soroban_meta.return_value is not None:
            return scval.to_native(soroban_meta.return_value)
    return None
