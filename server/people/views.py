import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from stellar_bridge.config import get_config
from stellar_bridge.errors import StellarBridgeError

from . import services, whatsapp
from .forms import SignupForm
from .models import LANGUAGES, Profile

log = logging.getLogger(__name__)


DEMO_USERNAMES = ("maria", "ana", "kofi", "reviewer")  # the accounts seed_demo creates


def home(request):
    """Visitors see the landing page; signed-in people go straight to their screen."""
    if not request.user.is_authenticated:
        return render(request, "landing.html")
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return redirect("review" if request.user.is_staff else "login")
    return redirect("guide_home" if profile.is_guide else "member_home")


class LoginPage(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if settings.DEMO_ACCOUNTS:
            users = get_user_model().objects.select_related("profile").filter(username__in=DEMO_USERNAMES)
            context["demo_users"] = sorted(users, key=lambda u: DEMO_USERNAMES.index(u.username))
        return context


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None, initial={"role": request.GET.get("as", ""), "languages": ["en"]})
    wallet_error = ""
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.register(
                role=data["role"], display_name=data["display_name"], username=data["username"],
                password=data["password"], city=data["city"], languages=data["languages"],
                whatsapp=data["whatsapp"], rate=data.get("rate"),
            )
        except Exception as exc:  # noqa: BLE001 - friendbot or Horizon can fail; nothing was saved
            log.warning("signup wallet provisioning failed", exc_info=True)
            wallet_error = str(exc) if isinstance(exc, StellarBridgeError) else "The Stellar test network did not respond."
        else:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("home")
    status = 400 if request.method == "POST" else 200
    return render(request, "people/signup.html", {
        "form": form,
        "languages": LANGUAGES,
        "chosen_languages": form["languages"].value() or ["en"],
        "wallet_error": wallet_error,
        "starting_balance": settings.SIGHTLINE["demo_starting_balance"],
        "asset_code": get_config().asset_code,
    }, status=status)


@require_POST
@login_required
def save_guide(request, guide_id):
    member = request.user.profile
    if member.is_guide:
        return JsonResponse({"ok": False, "message": "Only people asking for help can save guides."}, status=400)
    get_object_or_404(Profile, pk=guide_id, role=Profile.Role.GUIDE)
    saved = services.set_saved(member, guide_id, request.POST.get("saved", "1") == "1")
    return JsonResponse({"ok": True, "saved": saved})


@require_POST
@login_required
def set_whatsapp(request):
    profile = request.user.profile
    number = request.POST.get("whatsapp", "")
    if not whatsapp.is_valid(number):
        return JsonResponse({"ok": False, "message": "Enter your WhatsApp number with country code, for example +34 600 111 222."}, status=400)
    profile.whatsapp = whatsapp.normalize(number)
    profile.save(update_fields=["whatsapp"])
    return JsonResponse({"ok": True, "whatsapp": profile.whatsapp_display})


@require_POST
def demo_login(request, username):
    """One-tap sign-in for the hackathon demo (development, or DEMO_ACCOUNTS=1 on the hosted demo)."""
    if not settings.DEMO_ACCOUNTS:
        return redirect("login")
    user = get_object_or_404(get_user_model(), username=username)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("home")
