from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "actor", "action", "resource_type", "resource_id"]
    list_filter = ["organization", "action", "resource_type"]
    search_fields = ["resource_id", "action"]
    readonly_fields = [
        "organization",
        "actor",
        "action",
        "resource_type",
        "resource_id",
        "before",
        "after",
        "ip_address",
        "user_agent",
        "request_id",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
