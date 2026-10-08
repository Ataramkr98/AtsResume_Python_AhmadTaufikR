from django.contrib import admin

from .models import Candidate, CandidateSkill, DuplicateSuggestion, Resume, ResumeParseResult


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "organization", "current_title", "created_at"]
    search_fields = ["first_name", "last_name", "full_name_normalized"]
    readonly_fields = ["primary_email_encrypted", "email_hash", "primary_phone_encrypted", "phone_hash"]
    actions = None


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ["original_name", "candidate", "organization", "parser_status", "created_at"]
    list_filter = ["parser_status", "organization"]
    readonly_fields = ["checksum_sha256", "raw_text", "failure_reason"]
    actions = None


admin.site.register(ResumeParseResult)
admin.site.register(CandidateSkill)
admin.site.register(DuplicateSuggestion)
