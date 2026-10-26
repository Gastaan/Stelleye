"""Pick the next guide for a call, and list guides for a member to choose from.

Order everywhere: the member's saved guides first, then guides who speak the
requested language, then the cheapest rate.
"""
from decimal import Decimal

from django.db.models import Q

from people.models import Profile
from stellar_bridge.amounts import display

from . import presence
from .models import CallSession


def next_guide_for(call_id: int) -> Profile | None:
    call = CallSession.objects.select_related("member__profile").get(pk=call_id)
    member = call.member.profile
    declined = set(call.declined_by.values_list("id", flat=True))
    candidates = [
        guide
        for guide in Profile.objects.filter(
            role=Profile.Role.GUIDE, is_verified=True, user_id__in=presence.available_guide_ids()
        ).exclude(user_id__in=declined | {call.member_id})
        if guide.speaks(call.language)
    ]
    if not candidates:
        return None
    favorites = set(member.favorite_guides.values_list("id", flat=True))
    candidates.sort(key=lambda g: (g.id not in favorites, g.rate_per_minute))
    return candidates[0]


def guide_card(guide: Profile, saved: bool, online: set[int], busy: set[int]) -> dict:
    available = guide.user_id in online and guide.user_id not in busy
    state = "available" if available else "busy" if guide.user_id in online else "offline"
    return {
        "id": guide.id,
        "name": guide.display_name,
        "initials": guide.initials,
        "city": guide.city,
        "languages": guide.language_codes,
        "language_names": guide.language_names,
        "rate": display(guide.rate_per_minute),
        "saved": saved,
        "state": state,
    }


def guide_directory(member_user_id: int) -> list[dict]:
    """Verified guides who are online, plus every guide this member saved (online or not)."""
    member = Profile.objects.get(user_id=member_user_id)
    saved = set(member.favorite_guides.values_list("id", flat=True))
    online, busy = presence.snapshot()
    guides = Profile.objects.filter(role=Profile.Role.GUIDE, is_verified=True).filter(
        Q(user_id__in=online) | Q(id__in=saved)
    ).exclude(user_id=member_user_id)
    cards = [guide_card(g, g.id in saved, online, busy) for g in guides]
    rank = {"available": 0, "busy": 1, "offline": 2}
    cards.sort(key=lambda c: (rank[c["state"]], not c["saved"], Decimal(c["rate"]), c["name"]))
    return cards


def available_guide(profile_id: int) -> Profile | None:
    """The guide a member picked, if they can take a call right now."""
    guide = Profile.objects.filter(pk=profile_id, role=Profile.Role.GUIDE, is_verified=True).first()
    if guide and guide.user_id in presence.available_guide_ids():
        return guide
    return None
