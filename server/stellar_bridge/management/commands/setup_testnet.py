"""Deploy CallEscrow on testnet and write server/.env.

Default: create admin and issuer accounts, issue a demo stablecoin, deploy. With --asset-issuer, use an
existing asset instead (e.g. Circle's testnet USDC, which the SEP-24 test anchor pays out): no issuer
secret, money arrives through the anchor. --reuse-admin keeps the current admin key, re-verifies guides on
the new contract and adds the new asset's trustline to every existing wallet.
"""
import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from stellar_sdk import Asset, Keypair, TransactionBuilder, scval

from stellar_bridge import accounts, rpc
from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError
from stellar_bridge.escrow import EscrowClient

DEFAULT_WASM = settings.BASE_DIR.parent / "contracts/target/wasm32v1-none/release/call_escrow.wasm"
ENV_PATH = settings.BASE_DIR / ".env"
CIRCLE_TESTNET_USDC = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5"
MANAGED_KEYS = (
    "ESCROW_CONTRACT_ID",
    "TOKEN_CONTRACT_ID",
    "ASSET_CODE",
    "ASSET_ISSUER",
    "ISSUER_SECRET",
    "ADMIN_SECRET",
    "HOLD_SECONDS",
    "FEE_BPS",
)


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--hold-seconds", type=int, default=120, help="Earnings hold (86400 in production).")
        parser.add_argument("--fee-bps", type=int, default=500, help="Platform fee in basis points.")
        parser.add_argument("--asset-code", default="USDC")
        parser.add_argument("--asset-issuer", help=f"Use an existing asset, e.g. Circle testnet USDC {CIRCLE_TESTNET_USDC}.")
        parser.add_argument("--reuse-admin", action="store_true", help="Keep ADMIN_SECRET from server/.env.")
        parser.add_argument("--wasm", default=str(DEFAULT_WASM))
        parser.add_argument("--force", action="store_true", help="Redeploy even if .env is configured.")

    def handle(self, *args, **opts):
        base = get_config()
        if base.is_deployed and not opts["force"]:
            raise CommandError("server/.env is already configured. Pass --force to redeploy.")
        wasm_path = Path(opts["wasm"])
        if not wasm_path.exists():
            raise CommandError(f"{wasm_path} not found. Build first: cd contracts && stellar contract build")

        if opts["reuse_admin"]:
            if not base.admin_secret:
                raise CommandError("--reuse-admin needs ADMIN_SECRET in server/.env.")
            admin = Keypair.from_secret(base.admin_secret)
            self.stdout.write(f"Reusing admin {admin.public_key[:8]}…")
        else:
            admin = Keypair.random()
            self.stdout.write("Funding a new admin account with friendbot...")
            accounts.fund_with_friendbot(admin.public_key, base)

        issuer_secret = ""
        if opts["asset_issuer"]:
            asset = Asset(opts["asset_code"], opts["asset_issuer"])
            self.stdout.write(f"Using existing asset {asset.code}:{asset.issuer[:8]}… (arrives through the anchor)")
        else:
            issuer = Keypair.random()
            accounts.fund_with_friendbot(issuer.public_key, base)
            asset = Asset(opts["asset_code"], issuer.public_key)
            issuer_secret = issuer.secret

        cfg = base.with_overrides(
            asset_code=asset.code,
            asset_issuer=asset.issuer,
            issuer_secret=issuer_secret,
            admin_secret=admin.secret,
            hold_seconds=opts["hold_seconds"],
            fee_bps=opts["fee_bps"],
        )
        server = rpc.soroban(cfg)

        def build(add_op):
            account = server.load_account(admin.public_key)
            builder = TransactionBuilder(account, cfg.network_passphrase, base_fee=rpc.BASE_FEE)
            return add_op(builder).set_timeout(rpc.TX_TIMEOUT_SECONDS).build()

        token_id = asset.contract_id(cfg.network_passphrase)
        self.stdout.write(f"Making sure the Stellar Asset Contract for {asset.code} exists...")
        try:
            rpc.submit(build(lambda b: b.append_create_stellar_asset_contract_from_asset_op(asset)), admin, cfg)
        except StellarBridgeError as exc:
            self.stdout.write(f"  already deployed ({str(exc)[:60]})")

        wasm = wasm_path.read_bytes()
        self.stdout.write("Uploading CallEscrow WASM...")
        try:
            rpc.submit(build(lambda b: b.append_upload_contract_wasm_op(wasm)), admin, cfg)
        except StellarBridgeError as exc:
            self.stdout.write(f"  already uploaded ({str(exc)[:60]})")

        self.stdout.write("Instantiating CallEscrow...")
        constructor_args = [
            scval.to_address(admin.public_key),
            scval.to_address(token_id),
            scval.to_uint64(opts["hold_seconds"]),
            scval.to_uint32(opts["fee_bps"]),
        ]
        created = rpc.submit(
            build(
                lambda b: b.append_create_contract_op(
                    wasm_id=hashlib.sha256(wasm).digest(),
                    address=admin.public_key,
                    constructor_args=constructor_args,
                )
            ),
            admin,
            cfg,
        )
        escrow_id = getattr(created.return_value, "address", None) or str(created.return_value)
        cfg = cfg.with_overrides(escrow_contract_id=escrow_id, token_contract_id=token_id)

        self.stdout.write("Adding the platform's trustline for fees...")
        accounts.ensure_trustline(admin, cfg)

        self._write_env(
            {
                "ESCROW_CONTRACT_ID": escrow_id,
                "TOKEN_CONTRACT_ID": token_id,
                "ASSET_CODE": asset.code,
                "ASSET_ISSUER": asset.issuer,
                "ISSUER_SECRET": issuer_secret,
                "ADMIN_SECRET": admin.secret,
                "HOLD_SECONDS": str(opts["hold_seconds"]),
                "FEE_BPS": str(opts["fee_bps"]),
            }
        )
        if opts["reuse_admin"]:
            self._migrate_people(cfg)
        self.stdout.write(self.style.SUCCESS(f"CallEscrow: {cfg.account_url(escrow_id)}"))
        self.stdout.write(self.style.SUCCESS(f"Token ({asset.code}): {cfg.account_url(token_id)}"))
        self.stdout.write("Wrote server/.env. Restart the server" + ("." if opts["reuse_admin"] else ", then run: python manage.py seed_demo"))

    def _migrate_people(self, cfg) -> None:
        """Existing guides are verified again on the new contract; every wallet can hold the new asset."""
        from people.models import Profile
        from wallets.models import Wallet

        escrow = EscrowClient(cfg)
        for wallet in Wallet.objects.select_related("owner__profile"):
            try:
                accounts.ensure_trustline(wallet.keypair(), cfg)
                profile = getattr(wallet.owner, "profile", None)
                if profile and profile.role == Profile.Role.GUIDE and profile.is_verified:
                    escrow.verify_guide(wallet.public_key)
                    self.stdout.write(f"  {profile.display_name}: trustline ok, re-verified on the new contract")
                else:
                    self.stdout.write(f"  {wallet.owner.username}: trustline ok")
            except Exception as exc:  # noqa: BLE001 - one broken test wallet must not stop the rest
                self.stdout.write(self.style.WARNING(f"  {wallet.owner.username}: skipped ({exc})"))

    def _write_env(self, values: dict[str, str]) -> None:
        kept = []
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                key = line.split("=", 1)[0].strip()
                if key not in MANAGED_KEYS:
                    kept.append(line)
        lines = kept + [f"{key}={value}" for key, value in values.items()]
        ENV_PATH.write_text("\n".join(lines) + "\n")
