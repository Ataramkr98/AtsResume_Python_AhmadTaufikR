from __future__ import annotations

from django.db import models

from apps.common.crypto import decrypt_value, encrypt_value, lookup_hash
from apps.common.models import OrganizationOwnedModel, UUIDTimeStampedModel


class Candidate(OrganizationOwnedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    full_name_normalized = models.CharField(max_length=220)
    primary_email_encrypted = models.TextField()
    email_hash = models.CharField(max_length=64)
    primary_phone_encrypted = models.TextField(blank=True)
    phone_hash = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=120, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    current_title = models.CharField(max_length=160, blank=True)
    current_company = models.CharField(max_length=160, blank=True)
    total_experience_months = models.PositiveIntegerField(null=True, blank=True)
    education_level = models.CharField(max_length=40, blank=True)
    summary = models.TextField(blank=True)
    consent_status = models.CharField(max_length=30, default="GRANTED")
    source_first = models.CharField(max_length=50, default="CAREERS_PAGE")
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_candidates"
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "full_name_normalized"]),
            models.Index(fields=["organization", "email_hash"]),
        ]

    def set_email(self, value: str):
        self.primary_email_encrypted = encrypt_value(value)
        self.email_hash = lookup_hash(value)

    @property
    def primary_email(self):
        return decrypt_value(self.primary_email_encrypted)

    def set_phone(self, value: str):
        self.primary_phone_encrypted = encrypt_value(value)
        self.phone_hash = lookup_hash(value) if value else ""

    @property
    def primary_phone(self):
        return decrypt_value(self.primary_phone_encrypted)

    def save(self, *args, **kwargs):
        self.full_name_normalized = f"{self.first_name} {self.last_name}".strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()


def resume_upload_path(instance, filename):
    return f"organizations/{instance.organization_id}/resumes/{instance.id}/{filename}"


class Resume(OrganizationOwnedModel):
    class ParserStatus(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        SCANNING = "SCANNING", "Scanning"
        EXTRACTING = "EXTRACTING", "Extracting"
        PARSING = "PARSING", "Parsing"
        REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review required"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        INFECTED = "INFECTED", "Infected"

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="resumes")
    file = models.FileField(upload_to=resume_upload_path, max_length=500)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120)
    size_bytes = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    parser_status = models.CharField(max_length=24, choices=ParserStatus, default=ParserStatus.UPLOADED)
    parser_version = models.CharField(max_length=40, default="deterministic-v1")
    detected_language = models.CharField(max_length=10, blank=True)
    raw_text = models.TextField(blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="uploaded_resumes"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["checksum_sha256"])]


class ResumeParseResult(UUIDTimeStampedModel):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="parse_results")
    schema_version = models.CharField(max_length=20, default="1.0")
    parser_version = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    confidence = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]


class ResumeExperience(UUIDTimeStampedModel):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="experiences")
    company = models.CharField(max_length=180)
    title = models.CharField(max_length=180, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]


class ResumeEducation(UUIDTimeStampedModel):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="educations")
    institution = models.CharField(max_length=180)
    degree = models.CharField(max_length=120, blank=True)
    field_of_study = models.CharField(max_length=120, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]


class CandidateSkill(UUIDTimeStampedModel):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="candidate_skills")
    skill = models.ForeignKey("jobs.Skill", on_delete=models.PROTECT)
    source = models.CharField(max_length=30, default="RESUME")
    proficiency = models.CharField(max_length=30, blank=True)
    years_experience = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=0.8)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["candidate", "skill", "source"], name="unique_candidate_skill_source")
        ]


class DuplicateSuggestion(OrganizationOwnedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        MERGED = "MERGED", "Merged"
        DISMISSED = "DISMISSED", "Dismissed"

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="duplicate_suggestions")
    possible_duplicate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="suggested_as_duplicate"
    )
    score = models.DecimalField(max_digits=5, decimal_places=2)
    signals = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    resolved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["candidate", "possible_duplicate"], name="unique_duplicate_candidate_pair"
            )
        ]
        # The candidate detail page always asks "which *pending* suggestions does this person
        # have?", so the composite keeps that lookup on a single index instead of a scan of
        # every suggestion ever raised against the candidate.
        indexes = [models.Index(fields=["candidate", "status"])]
