from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from calls.models import CallSession
from calls.services import escrow_for, guide_share, summary
from wallets.services import keypair_for

from .models import Report


def report_call(call_id: int, member_id: int, reason: str) -> dict:
    """Freeze the held earnings on-chain and open a case for human review."""
    call = CallSession.objects.select_related("member").get(pk=call_id)
    if call.member_id != member_id:
        raise PermissionError("Only the caller can report this call.")
    if not call.can_report:
        raise PermissionError("This call can no longer be reported.")
    tx_hash = escrow_for(call).report(keypair_for(call.member), call.onchain_id)
    with transaction.atomic():
        call.status = CallSession.Status.DISPUTED
        call.save(update_fields=["status"])
        Report.objects.create(call=call, reason=reason.strip() or "No details given.", report_tx=tx_hash)
    return summary(call)


def resolve(report_id: int, refund_to_member: Decimal, reviewer, note: str = "") -> Report:
    report = Report.objects.select_related("call").get(pk=report_id)
    call = report.call
    if not report.is_open:
        raise PermissionError("This report is already resolved.")
    if refund_to_member < 0 or refund_to_member > call.earned:
        raise ValueError("Refund must be between zero and what the guide earned.")
    tx_hash = escrow_for(call).resolve(call.onchain_id, refund_to_member)
    with transaction.atomic():
        call.refunded = (call.refunded or Decimal("0")) + refund_to_member
        call.guide_payout = guide_share(call.earned - refund_to_member)
        call.payout_tx = tx_hash
        call.status = CallSession.Status.RESOLVED
        call.save(update_fields=["refunded", "guide_payout", "payout_tx", "status"])
        report.refund_to_member = refund_to_member
        report.reviewer_note = note
        report.resolved_by = reviewer
        report.resolved_at = timezone.now()
        report.resolve_tx = tx_hash
        report.save()
    return report
