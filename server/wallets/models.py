from django.conf import settings
from django.db import models
from stellar_sdk import Keypair


class Wallet(models.Model):
    """App-managed Stellar account for v1.

    The key is held by the server so visually impaired users never handle seed phrases.
    Planned upgrade: passkey smart accounts (Face ID / Touch ID) with sponsored fees.
    """

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    public_key = models.CharField(max_length=56, unique=True)
    secret_seed = models.CharField(max_length=56)
    created_at = models.DateTimeField(auto_now_add=True)

    def keypair(self) -> Keypair:
        return Keypair.from_secret(self.secret_seed)

    def __str__(self) -> str:
        return f"{self.owner} {self.public_key[:6]}...{self.public_key[-4:]}"


class AnchorTransfer(models.Model):
    """Real money in (deposit) or out (withdrawal) through the SEP-24 anchor."""

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Add money"
        WITHDRAW = "withdraw", "Cash out"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="anchor_transfers")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    anchor_domain = models.CharField(max_length=120)
    anchor_id = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=40, default="incomplete")
    requested_amount = models.DecimalField(max_digits=16, decimal_places=7)
    amount_in = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    amount_out = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    amount_fee = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    message = models.CharField(max_length=300, blank=True)
    stellar_tx = models.CharField(max_length=64, blank=True, help_text="The anchor's payment to us (deposits).")
    payment_tx = models.CharField(max_length=64, blank=True, help_text="Our payment to the anchor (withdrawals).")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.requested_amount} ({self.status})"
