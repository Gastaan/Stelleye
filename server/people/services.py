from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from stellar_bridge import accounts
from stellar_bridge.escrow import EscrowClient
from wallets.models import Wallet

from .models import Profile


def register(*, role: str, display_name: str, username: str, password: str, city: str = "",
             languages: list[str], whatsapp: str = "", rate: Decimal | None = None):
    """Create a person and their Stellar wallet. The wallet comes first, so a network failure leaves nothing behind."""
    starting = Decimal(settings.SIGHTLINE["demo_starting_balance"]) if role == Profile.Role.MEMBER else Decimal("0")
    keypair = accounts.provision(starting)
    with transaction.atomic():
        user = get_user_model().objects.create_user(username=username, password=password)
        Profile.objects.create(
            user=user, role=role, display_name=display_name.strip(), city=city.strip(),
            languages=",".join(languages), whatsapp=whatsapp, rate_per_minute=rate or Decimal("0.20"),
        )
        Wallet.objects.create(owner=user, public_key=keypair.public_key, secret_seed=keypair.secret)
    return user


def set_saved(member: Profile, guide_id: int, saved: bool) -> bool:
    """Save a guide to (or remove them from) the member's guides. Returns the new state."""
    guide = Profile.objects.get(pk=guide_id, role=Profile.Role.GUIDE)
    (member.favorite_guides.add if saved else member.favorite_guides.remove)(guide)
    _push(member.user_id, {"type": "guides.changed"})
    return saved


def verify_guide(profile: Profile) -> str:
    """Admin approval (stand-in for ID checks) recorded in the on-chain guide registry."""
    tx_hash = EscrowClient().verify_guide(profile.user.wallet.public_key)
    profile.is_verified = True
    profile.save(update_fields=["is_verified"])
    _push(profile.user_id, {"type": "deliver", "payload": {"type": "verified", "message": "You are verified. You can go online now."}})
    return tx_hash


def revoke_guide(profile: Profile) -> str:
    tx_hash = EscrowClient().revoke_guide(profile.user.wallet.public_key)
    profile.is_verified = False
    profile.save(update_fields=["is_verified"])
    return tx_hash


def _push(user_id: int, event: dict) -> None:
    """Tell the person's open pages that something changed for them."""
    layer = get_channel_layer()
    if layer is not None:
        async_to_sync(layer.group_send)(f"user-{user_id}", event)
