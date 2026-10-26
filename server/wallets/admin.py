from django.contrib import admin

from .models import Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("owner", "public_key", "created_at")
    exclude = ("secret_seed",)
    readonly_fields = ("owner", "public_key", "created_at")
