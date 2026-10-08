from django.contrib import admin

from .models import (
    Department,
    Job,
    JobSkillRequirement,
    Location,
    PipelineStage,
    PipelineTemplate,
    PipelineTemplateStage,
    ScorecardCriterion,
    ScorecardTemplate,
    Skill,
)

admin.site.register(Department)
admin.site.register(Location)
admin.site.register(Skill)
admin.site.register(PipelineTemplate)
admin.site.register(PipelineTemplateStage)
admin.site.register(PipelineStage)
admin.site.register(JobSkillRequirement)
admin.site.register(ScorecardTemplate)
admin.site.register(ScorecardCriterion)


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ["title", "organization", "status", "department", "location", "created_at"]
    list_filter = ["status", "organization"]
    search_fields = ["title", "internal_code"]
    readonly_fields = ["approved_at", "published_at", "closed_at", "version"]
    actions = None
