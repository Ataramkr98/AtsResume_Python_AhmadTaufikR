from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.common.models import OrganizationOwnedModel


class Task(OrganizationOwnedModel):
    """A recruiter follow-up attached to a workspace, optionally to a job or application."""

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        DONE = "DONE", "Done"
        CANCELLED = "CANCELLED", "Cancelled"

    assignee = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="assigned_tasks"
    )
    application = models.ForeignKey(
        "applications.Application",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    job = models.ForeignKey(
        "jobs.Job", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks"
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=12, choices=Priority, default=Priority.NORMAL)
    status = models.CharField(max_length=16, choices=Status, default=Status.OPEN)
    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_at", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "status", "due_at"]),
            models.Index(fields=["assignee", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_at
            and self.status in {self.Status.OPEN, self.Status.IN_PROGRESS}
            and self.due_at < timezone.now()
        )

    @property
    def is_open(self) -> bool:
        return self.status in {self.Status.OPEN, self.Status.IN_PROGRESS}
