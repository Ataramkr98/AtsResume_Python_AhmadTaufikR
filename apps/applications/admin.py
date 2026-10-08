from django.contrib import admin

from .models import (
    Application,
    ApplicationStageHistory,
    IdempotencyRecord,
    MatchScore,
    RejectionReason,
)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ["candidate", "job", "current_stage", "status", "applied_at"]
    list_filter = ["status", "organization"]
    readonly_fields = ["version", "last_stage_changed_at", "hired_at", "rejected_at"]
    actions = None


admin.site.register(ApplicationStageHistory)
admin.site.register(MatchScore)
admin.site.register(RejectionReason)
admin.site.register(IdempotencyRecord)
