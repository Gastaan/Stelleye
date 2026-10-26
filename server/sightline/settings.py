from pathlib import Path
import json
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "stellar_bridge",
    "wallets",
    "people",
    "calls",
    "disputes",
    "ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "sightline.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "people.context_processors.profile",
            ],
        },
    },
]

ASGI_APPLICATION = "sightline.asgi.application"
WSGI_APPLICATION = None

# Single-process demo: in-memory channel layer. Use Redis for multiple workers.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# ---- Stellar -----------------------------------------------------------------
# Written by `python manage.py setup_testnet`; see stellar_bridge/config.py.
STELLAR = {
    "rpc_url": os.environ.get("STELLAR_RPC_URL", "https://soroban-testnet.stellar.org"),
    "horizon_url": os.environ.get("STELLAR_HORIZON_URL", "https://horizon-testnet.stellar.org"),
    "network_passphrase": os.environ.get(
        "STELLAR_NETWORK_PASSPHRASE", "Test SDF Network ; September 2015"
    ),
    "friendbot_url": os.environ.get("STELLAR_FRIENDBOT_URL", "https://friendbot.stellar.org"),
    "explorer_url": os.environ.get(
        "STELLAR_EXPLORER_URL", "https://stellar.expert/explorer/testnet"
    ),
    "escrow_contract_id": os.environ.get("ESCROW_CONTRACT_ID", ""),
    "token_contract_id": os.environ.get("TOKEN_CONTRACT_ID", ""),
    "asset_code": os.environ.get("ASSET_CODE", "USDC"),
    "asset_issuer": os.environ.get("ASSET_ISSUER", ""),
    "issuer_secret": os.environ.get("ISSUER_SECRET", ""),
    "admin_secret": os.environ.get("ADMIN_SECRET", ""),
    "hold_seconds": int(os.environ.get("HOLD_SECONDS", "86400")),
    "fee_bps": int(os.environ.get("FEE_BPS", "500")),
    # SEP-24 anchor for real money in and out (cards, bank, cash). testanchor pays Circle's testnet USDC;
    # production points this at a licensed anchor such as MoneyGram Ramps.
    "anchor_domain": os.environ.get("ANCHOR_HOME_DOMAIN", "testanchor.stellar.org"),
}

# ---- Product defaults ----------------------------------------------------------
SIGHTLINE = {
    "default_budget": os.environ.get("DEFAULT_BUDGET", "3.00"),
    "demo_starting_balance": os.environ.get("DEMO_STARTING_BALANCE", "25.00"),
    "ring_timeout_seconds": int(os.environ.get("RING_TIMEOUT_SECONDS", "30")),
    # The video call is WhatsApp only. In-app WebRTC video exists for testing and is off unless this is set.
    "inapp_video_test_mode": os.environ.get("INAPP_VIDEO_TEST_MODE", "0") == "1",
    # WebRTC: public STUN is enough on most networks; add a TURN server for strict NATs, e.g.
    # WEBRTC_ICE_SERVERS='[{"urls": "turn:turn.example.org:3478", "username": "u", "credential": "p"}]'
    "ice_servers": json.loads(os.environ.get(
        "WEBRTC_ICE_SERVERS", '[{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]'
    )),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "calls": {"handlers": ["console"], "level": "INFO"},
        "stellar_bridge": {"handlers": ["console"], "level": "INFO"},
    },
}
