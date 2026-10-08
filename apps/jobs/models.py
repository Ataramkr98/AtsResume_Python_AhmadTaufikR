from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from apps.common.models import OrganizationOwnedModel, UUIDTimeStampedModel


class Department(OrganizationOwnedModel):
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="unique_department_name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(OrganizationOwnedModel):
    class WorkplaceType(models.TextChoices):
        ONSITE = "ONSITE", "On-site"
        HYBRID = "HYBRID", "Hybrid"
        REMOTE = "REMOTE", "Remote"

    name = models.CharField(max_length=120)
    city = models.CharField(max_length=120, blank=True)
    region = models.CharField(max_length=120, blank=True)
    country_code = models.CharField(max_length=2)
    workplace_type = models.CharField(max_length=12, choices=WorkplaceType, default=WorkplaceType.HYBRID)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="unique_location_name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Skill(OrganizationOwnedModel):
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100)
    category = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "normalized_name"], name="unique_skill_name")
        ]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.normalized_name = self.name.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PipelineTemplate(OrganizationOwnedModel):
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_pipeline_template")
        ]

    def __str__(self):
        return self.name


class StageType(models.TextChoices):
    APPLIED = "APPLIED", "Applied"
    SCREENING = "SCREENING", "Screening"
    INTERVIEW = "INTERVIEW", "Interview"
    ASSESSMENT = "ASSESSMENT", "Assessment"
    OFFER = "OFFER", "Offer"
    HIRED = "HIRED", "Hired"
    CUSTOM = "CUSTOM", "Custom"


class PipelineTemplateStage(UUIDTimeStampedModel):
    template = models.ForeignKey(PipelineTemplate, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=100)
    stage_type = models.CharField(max_length=16, choices=StageType, default=StageType.CUSTOM)
    position = models.PositiveSmallIntegerField()
    sla_hours = models.PositiveIntegerField(null=True, blank=True)
    requires_interview = models.BooleanField(default=False)
    requires_scorecard = models.BooleanField(default=False)
    terminal = models.BooleanField(default=False)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["template", "position"], name="unique_template_stage_position")
        ]


class Job(OrganizationOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending approval"
        OPEN = "OPEN", "Open"
        PAUSED = "PAUSED", "Paused"
        CLOSED = "CLOSED", "Closed"
        ARCHIVED = "ARCHIVED", "Archived"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "FULL_TIME", "Full-time"
        PART_TIME = "PART_TIME", "Part-time"
        CONTRACT = "CONTRACT", "Contract"
        INTERNSHIP = "INTERNSHIP", "Internship"

    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="jobs")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="jobs")
    hiring_manager = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="managed_jobs"
    )
    recruiter = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="recruited_jobs"
    )
    pipeline_template = models.ForeignKey(PipelineTemplate, on_delete=models.PROTECT, related_name="jobs")
    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200)
    internal_code = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=24, choices=Status, default=Status.DRAFT)
    employment_type = models.CharField(max_length=20, choices=EmploymentType)
    headcount = models.PositiveIntegerField(default=1)
    description_html = models.TextField()
    requirements_html = models.TextField(blank=True)
    min_experience_months = models.PositiveIntegerField(null=True, blank=True)
    education_level = models.CharField(max_length=40, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="approved_jobs"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["organization", "slug"], name="unique_job_slug")]
        indexes = [models.Index(fields=["organization", "status", "created_at"])]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:180]
            candidate = base
            suffix = 2
            while Job.objects.filter(organization=self.organization, slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class JobTeamMember(UUIDTimeStampedModel):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="team_members")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    role = models.CharField(max_length=40)
    permission_level = models.CharField(max_length=24, default="REVIEW")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "user"], name="unique_job_team_member")]


class PipelineStage(UUIDTimeStampedModel):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=100)
    stage_type = models.CharField(max_length=16, choices=StageType, default=StageType.CUSTOM)
    position = models.PositiveSmallIntegerField()
    sla_hours = models.PositiveIntegerField(null=True, blank=True)
    requires_interview = models.BooleanField(default=False)
    requires_scorecard = models.BooleanField(default=False)
    terminal = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["job", "position"], name="unique_job_stage_position")]

    def delete(self, *args, **kwargs):
        if self.applications.exists():
            raise ValidationError("Stages containing applications cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.job}: {self.name}"


class JobSkillRequirement(UUIDTimeStampedModel):
    class RequirementType(models.TextChoices):
        MUST = "MUST", "Must-have"
        PREFERRED = "PREFERRED", "Preferred"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="skill_requirements")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT)
    requirement_type = models.CharField(max_length=12, choices=RequirementType)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "skill"], name="unique_job_skill")]


class ScorecardTemplate(UUIDTimeStampedModel):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="scorecard_templates")
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "version"], name="unique_scorecard_version")]


class ScorecardCriterion(UUIDTimeStampedModel):
    template = models.ForeignKey(ScorecardTemplate, on_delete=models.CASCADE, related_name="criteria")
    competency = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["template", "position"], name="unique_scorecard_criterion_position")
        ]
