from decimal import Decimal

from django.conf import settings
from django.db import models

LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("ar", "Arabic"),
    ("fa", "Persian"),
]


class Profile(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member", "Needs guidance"
        GUIDE = "guide", "Guide"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=Role.choices)
    display_name = models.CharField(max_length=80)
    city = models.CharField(max_length=80, blank=True)
    languages = models.CharField(max_length=60, default="en", help_text="Comma-separated codes, e.g. en,es")
    whatsapp = models.CharField(
        max_length=20, blank=True, help_text="Digits with country code, e.g. 34600111222. Shared only with a matched peer."
    )
    rate_per_minute = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.20"))
    is_verified = models.BooleanField(default=False, help_text="Mirrors the on-chain guide registry.")
    favorite_guides = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="favorited_by")

    @property
    def is_guide(self) -> bool:
        return self.role == self.Role.GUIDE

    @property
    def language_codes(self) -> list[str]:
        return [code.strip() for code in self.languages.split(",") if code.strip()]

    def speaks(self, code: str) -> bool:
        return code in self.language_codes

    @property
    def language_names(self) -> list[str]:
        names = dict(LANGUAGES)
        return [names.get(code, code) for code in self.language_codes]

    @property
    def initials(self) -> str:
        return "".join(part[0] for part in self.display_name.split()[:2]).upper() or "?"

    @property
    def whatsapp_display(self) -> str:
        from .whatsapp import display

        return display(self.whatsapp)

    @property
    def spoken_intro(self) -> str:
        return f"{self.display_name} in {self.city}" if self.city else self.display_name

    def __str__(self) -> str:
        return f"{self.display_name} ({self.get_role_display()})"
