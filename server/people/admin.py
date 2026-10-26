from django.contrib import admin, messages

from stellar_bridge.errors import StellarBridgeError

from . import services
from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "role", "city", "languages", "rate_per_minute", "is_verified")
    list_filter = ("role", "is_verified")
    actions = ["verify_on_chain", "revoke_on_chain"]

    @admin.action(description="Verify selected guides on-chain")
    def verify_on_chain(self, request, queryset):
        for profile in queryset.filter(role=Profile.Role.GUIDE):
            try:
                services.verify_guide(profile)
                self.message_user(request, f"Verified {profile.display_name}")
            except StellarBridgeError as exc:
                self.message_user(request, f"{profile.display_name}: {exc}", level=messages.ERROR)

    @admin.action(description="Revoke selected guides on-chain")
    def revoke_on_chain(self, request, queryset):
        for profile in queryset.filter(role=Profile.Role.GUIDE):
            services.revoke_guide(profile)
