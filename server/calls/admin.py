from django.contrib import admin

from .models import CallSession, ChatMessage


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("sender", "body", "link", "created_at")


@admin.register(CallSession)
class CallSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "member", "guide", "status", "direct", "budget", "earned", "onchain_id", "created_at")
    list_filter = ("status", "direct")
    readonly_fields = [field.name for field in CallSession._meta.fields]
    inlines = [ChatMessageInline]
