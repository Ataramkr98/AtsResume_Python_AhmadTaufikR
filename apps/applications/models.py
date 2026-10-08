from django.db import models
from django.utils import timezone

from apps.common.models import OrganizationOwnedModel, UUIDTimeStampedModel


class RejectionReason(OrganizationOwnedModel):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80, blank=True)
    candidate_visible = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_rejection_reason")
        ]

    def __str__(self):
        return self.name


class Application(OrganizationOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        HIRED = "HIRED", "Hired"
        ARCHIVED = "ARCHIVED", "Archived"

    job = models.ForeignKey("jobs.Job", on_delete=models.PROTECT, related_name="applications")
    candidate = models.ForeignKey("candidates.Candidate", on_delete=models.PROTECT, related_name="applications")
    resume = models.ForeignKey("candidates.Resume", null=True, blank=True, on_delete=models.PROTECT)
    current_stage = models.ForeignKey(
        "jobs.PipelineStage", on_delete=models.PROTECT, related_name="applications"
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    source = models.CharField(max_length=40)
    source_detail = models.CharField(max_length=160, blank=True)
    assigned_recruiter = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_applications"
    )
    applied_at = models.DateTimeField(default=timezone.now)
    last_stage_changed_at = models.DateTimeField(default=timezone.now)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.ForeignKey(RejectionReason, null=True, blank=True, on_delete=models.PROTECT)
    hired_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-applied_at"]
        constraints = [models.UniqueConstraint(fields=["job", "candidate"], name="unique_job_candidate")]
        indexes = [
            models.Index(fields=["job", "current_stage", "status"]),
            models.Index(fields=["candidate", "created_at"]),
        ]

    def __str__(self):
        return f"{self.candidate} — {self.job}"


class ApplicationStageHistory(UUIDTimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="stage_history")
    from_stage = models.ForeignKey(
        "jobs.PipelineStage", null=True, blank=True, on_delete=models.PROTECT, related_name="moves_from"
    )
    to_stage = models.ForeignKey("jobs.PipelineStage", on_delete=models.PROTECT, related_name="moves_to")
    moved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=255, blank=True)
    moved_at = models.DateTimeField(default=timezone.now)
    duration_previous_stage_seconds = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-moved_at"]
        indexes = [models.Index(fields=["application", "moved_at"])]


class MatchScore(UUIDTimeStampedModel):
    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="match_score")
    model_version = models.CharField(max_length=40)
    total_score = models.DecimalField(max_digits=5, decimal_places=2)
    skill_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    experience_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    education_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    location_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    explanation = models.JSONField(default=dict)


class IdempotencyRecord(OrganizationOwnedModel):
    key = models.CharField(max_length=100)
    action = models.CharField(max_length=80)
    resource_type = models.CharField(max_length=80)
    resource_id = models.CharField(max_length=64)
    response = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "action", "key"], name="unique_idempotent_action")
        ]
