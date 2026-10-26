from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("call", "created_at", "refund_to_member", "resolved_by", "resolved_at")
    readonly_fields = ("call", "report_tx", "resolve_tx", "created_at", "resolved_at")
