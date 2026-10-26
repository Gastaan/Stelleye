from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from people import services as people
from people.models import Profile
from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError

from . import services
from .models import Report


@require_POST
@login_required
def report(request, call_id):
    try:
        data = services.report_call(call_id, request.user.id, request.POST.get("reason", ""))
    except (StellarBridgeError, PermissionError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    return JsonResponse({"ok": True, **data})


@staff_member_required
def review_list(request):
    cfg = get_config()
    reports = Report.objects.select_related("call__member__profile", "call__guide__profile")
    pending = Profile.objects.filter(role=Profile.Role.GUIDE, is_verified=False).select_related("user__wallet")
    return render(request, "disputes/review.html", {"reports": reports, "pending_guides": pending, "cfg": cfg})


@require_POST
@staff_member_required
def resolve(request, report_id):
    try:
        refund = Decimal(request.POST.get("refund", "0"))
        services.resolve(report_id, refund, request.user, request.POST.get("note", ""))
        messages.success(request, "Decision recorded on-chain.")
    except (InvalidOperation, ValueError, PermissionError, StellarBridgeError) as exc:
        messages.error(request, str(exc))
    return redirect("review")


@require_POST
@staff_member_required
def verify_guide(request, profile_id):
    profile = get_object_or_404(Profile, pk=profile_id, role=Profile.Role.GUIDE)
    try:
        tx_hash = people.verify_guide(profile)
        messages.success(request, f"{profile.display_name} is verified in the on-chain guide registry ({tx_hash[:8]}…).")
    except Exception as exc:  # noqa: BLE001 - show any network failure to the reviewer
        messages.error(request, str(exc) if isinstance(exc, StellarBridgeError) else "The Stellar test network did not respond.")
    return redirect("review")
