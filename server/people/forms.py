from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator

from . import whatsapp
from .models import LANGUAGES, Profile


class SignupForm(forms.Form):
    role = forms.ChoiceField(
        choices=[(Profile.Role.MEMBER, "I need visual help"), (Profile.Role.GUIDE, "I want to be a guide")],
        error_messages={"required": "Choose whether you need help or want to be a guide."},
    )
    display_name = forms.CharField(
        max_length=80, error_messages={"required": "Enter the name people will see."}
    )
    username = forms.CharField(
        max_length=30, validators=[UnicodeUsernameValidator()],
        error_messages={"required": "Choose a username for signing in."},
    )
    password = forms.CharField(
        min_length=6, strip=False,
        error_messages={"required": "Choose a password.", "min_length": "Use at least 6 characters for your password."},
    )
    city = forms.CharField(max_length=80, required=False)
    languages = forms.MultipleChoiceField(
        choices=LANGUAGES, error_messages={"required": "Choose at least one language you speak."}
    )
    whatsapp = forms.CharField(
        max_length=24, error_messages={"required": "Enter your WhatsApp number. The video call happens on WhatsApp."}
    )
    rate = forms.DecimalField(
        min_value=Decimal("0.05"), max_value=Decimal("2.00"), decimal_places=2, required=False,
        error_messages={"min_value": "The lowest rate is 0.05 per minute.", "max_value": "The highest rate is 2.00 per minute."},
    )

    def clean_username(self) -> str:
        username = self.cleaned_data["username"].strip().lower()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken. Try another one.")
        return username

    def clean_whatsapp(self) -> str:
        number = self.cleaned_data.get("whatsapp", "")
        if not whatsapp.is_valid(number):
            raise forms.ValidationError("Enter the number with its country code, for example +34 600 111 222.")
        return whatsapp.normalize(number)

    def clean(self):
        data = super().clean()
        if data.get("role") == Profile.Role.GUIDE and data.get("rate") is None and "rate" not in self.errors:
            data["rate"] = Decimal("0.20")
        return data
