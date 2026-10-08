from django.contrib import admin

from .models import Interview, InterviewParticipant, ScorecardAnswer, ScorecardSubmission


@admin.register(ScorecardSubmission)
class ScorecardSubmissionAdmin(admin.ModelAdmin):
    list_display = ["application", "submitted_by", "status", "submitted_at"]
    readonly_fields = ["locked_at", "submitted_at"]
    actions = None


admin.site.register(Interview)
admin.site.register(InterviewParticipant)
admin.site.register(ScorecardAnswer)
