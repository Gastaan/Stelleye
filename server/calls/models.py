from django.conf import settings
from django.db import models
from django.utils import timezone


class CallSession(models.Model):
    """Off-chain mirror of a CallEscrow session plus the call's matching state."""

    class Status(models.TextChoices):
        RINGING = "ringing", "Ringing"
        CONNECTING = "connecting", "Securing payment"
        ACTIVE = "active", "In progress"
        ENDING = "ending", "Settling payment"
        HELD = "held", "Paid, on hold"
        DISPUTED = "disputed", "Reported, under review"
        RELEASED = "released", "Paid out"
        RESOLVED = "resolved", "Resolved after review"
        MISSED = "missed", "Not answered"
        FAILED = "failed", "Failed"

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calls_made")
    guide = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls_taken"
    )
    declined_by = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="declined_calls")
    direct = models.BooleanField(default=False, help_text="The member chose this guide; do not ring anyone else.")
    language = models.CharField(max_length=5)
    video_state = models.CharField(
        max_length=10, blank=True,
        help_text="Last video hand-off step: opened, connected, failed (WhatsApp), inapp or whatsapp.",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RINGING)

    rate_per_minute = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    budget = models.DecimalField(max_digits=10, decimal_places=2)
    earned = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    refunded = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    guide_payout = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)

    escrow_contract = models.CharField(
        max_length=56, blank=True, help_text="CallEscrow contract this call was opened on (survives redeploys)."
    )
    onchain_id = models.PositiveBigIntegerField(null=True, blank=True)
    open_tx = models.CharField(max_length=64, blank=True)
    close_tx = models.CharField(max_length=64, blank=True)
    payout_tx = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    hold_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["escrow_contract", "onchain_id"], name="unique_onchain_session"),
        ]

    @property
    def seconds(self) -> int:
        if self.started_at and self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds())
        return 0

    @property
    def hold_over(self) -> bool:
        return bool(self.hold_until and timezone.now() >= self.hold_until)

    @property
    def can_confirm(self) -> bool:
        return self.status == self.Status.HELD

    @property
    def can_report(self) -> bool:
        return self.status == self.Status.HELD and not self.hold_over

    @property
    def can_claim(self) -> bool:
        return self.status == self.Status.HELD and self.hold_over

    def __str__(self) -> str:
        return f"Call {self.pk} ({self.get_status_display()})"


class ChatMessage(models.Model):
    """What the two people type to each other during a call. No sender means a system note."""

    call = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.CharField(max_length=500)
    link = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.sender or 'system'}: {self.body[:40]}"
