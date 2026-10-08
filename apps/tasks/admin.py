from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "organization", "assignee", "status", "priority", "due_at"]
    list_filter = ["status", "priority", "organization"]
    search_fields = ["title", "description"]
    readonly_fields = ["completed_at", "created_by"]
