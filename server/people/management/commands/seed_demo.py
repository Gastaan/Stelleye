"""Create demo people with funded testnet wallets; guides are verified on-chain."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from people import services
from people.models import Profile
from stellar_bridge.config import get_config
from wallets.services import ensure_wallet

DEMO_PASSWORD = "sightline"

PEOPLE = [
    {"username": "maria", "name": "Maria", "role": Profile.Role.MEMBER, "city": "Madrid", "languages": "es,en"},
    {"username": "ana", "name": "Ana", "role": Profile.Role.GUIDE, "city": "Mexico City", "languages": "es,en", "rate": "0.20"},
    {"username": "kofi", "name": "Kofi", "role": Profile.Role.GUIDE, "city": "Accra", "languages": "en,fr", "rate": "0.15"},
]


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **opts):
        cfg = get_config()
        if not cfg.is_deployed:
            raise CommandError("Run `python manage.py setup_testnet` first, then restart.")
        User = get_user_model()
        starting_balance = Decimal(settings.SIGHTLINE["demo_starting_balance"])

        for spec in PEOPLE:
            user, created = User.objects.get_or_create(username=spec["username"])
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            profile, _ = Profile.objects.update_or_create(
                user=user,
                defaults={
                    "role": spec["role"],
                    "display_name": spec["name"],
                    "city": spec["city"],
                    "languages": spec["languages"],
                    "rate_per_minute": Decimal(spec.get("rate", "0.20")),
                },
            )
            self.stdout.write(f"Provisioning wallet for {profile.display_name}...")
            balance = starting_balance if profile.role == Profile.Role.MEMBER else Decimal("0")
            wallet = ensure_wallet(user, balance)
            if profile.is_guide and not profile.is_verified:
                services.verify_guide(profile)
                self.stdout.write(f"  verified {profile.display_name} on-chain")
            self.stdout.write(f"  {cfg.account_url(wallet.public_key)}")

        maria = Profile.objects.get(user__username="maria")
        maria.favorite_guides.add(Profile.objects.get(user__username="ana"))

        reviewer, created = User.objects.get_or_create(
            username="reviewer", defaults={"is_staff": True, "is_superuser": True}
        )
        if created:
            reviewer.set_password(DEMO_PASSWORD)
            reviewer.save()
        self.stdout.write(self.style.SUCCESS(f"Demo ready. Password for every account: {DEMO_PASSWORD}"))
