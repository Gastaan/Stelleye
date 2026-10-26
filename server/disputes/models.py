from django.conf import settings
from django.db import models

from calls.models import CallSession


class Report(models.Model):
    """A member's report on a call; a human reviewer decides the refund split."""

    call = models.OneToOneField(CallSession, on_delete=models.CASCADE, related_name="report")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    report_tx = models.CharField(max_length=64, blank=True)

    refund_to_member = models.DecimalField(max_digits=16, decimal_places=7, null=True, blank=True)
    reviewer_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolve_tx = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["resolved_at", "-created_at"]

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
