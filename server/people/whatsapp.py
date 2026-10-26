"""WhatsApp is the call channel: Stelleye matches, meters and pays; the call itself is a WhatsApp video call.

Numbers are stored as digits with country code (E.164 without the +), which is what wa.me expects.
"""
import re
from urllib.parse import quote

_NON_DIGITS = re.compile(r"\D")
MIN_DIGITS, MAX_DIGITS = 8, 15


def normalize(number: str) -> str:
    digits = _NON_DIGITS.sub("", number or "")
    return digits[2:] if digits.startswith("00") else digits


def is_valid(number: str) -> bool:
    return MIN_DIGITS <= len(normalize(number)) <= MAX_DIGITS


def display(number: str) -> str:
    digits = normalize(number)
    return f"+{digits}" if digits else ""


def chat_link(number: str, text: str = "") -> str:
    """Universal link: opens the WhatsApp app on phones, but only a web page on computers."""
    url = f"https://wa.me/{normalize(number)}"
    return f"{url}?text={quote(text)}" if text else url


def app_link(number: str, text: str = "") -> str:
    """Opens the installed WhatsApp app directly, which on a Mac or PC is the one that can make video calls."""
    url = f"whatsapp://send?phone={normalize(number)}"
    return f"{url}&text={quote(text)}" if text else url
