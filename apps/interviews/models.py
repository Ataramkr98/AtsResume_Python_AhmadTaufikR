from django.db import models

from apps.common.models import UUIDTimeStampedModel


class Interview(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    application = models.ForeignKey(
        "applications.Application", on_delete=models.CASCADE, related_name="interviews"
    )
    scorecard_template = models.ForeignKey("jobs.ScorecardTemplate", on_delete=models.PROTECT)
    template_snapshot = models.JSONField(default=dict)
    interview_type = models.CharField(max_length=40, default="VIDEO")
    title = models.CharField(max_length=160)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default="UTC")
    location = models.CharField(max_length=255, blank=True)
    meeting_url = models.URLField(blank=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.SCHEDULED)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="created_interviews")

    class Meta:
        ordering = ["start_at"]
        indexes = [models.Index(fields=["start_at", "status"])]


class InterviewParticipant(UUIDTimeStampedModel):
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="interview_participations")
    role = models.CharField(max_length=40, default="INTERVIEWER")
    attendance_status = models.CharField(max_length=24, default="INVITED")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["interview", "user"], name="unique_interview_user")]


class ScorecardSubmission(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        REOPENED = "REOPENED", "Reopened"

    class Recommendation(models.TextChoices):
        STRONG_YES = "STRONG_YES", "Strong yes"
        YES = "YES", "Yes"
        MIXED = "MIXED", "Mixed"
        NO = "NO", "No"
        STRONG_NO = "STRONG_NO", "Strong no"

    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="scorecard_submissions")
    application = models.ForeignKey(
        "applications.Application", on_delete=models.CASCADE, related_name="scorecard_submissions"
    )
    submitted_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="scorecards")
    template_version = models.PositiveIntegerField()
    overall_recommendation = models.CharField(max_length=20, choices=Recommendation, blank=True)
    summary = models.TextField(blank=True)
    conflict_of_interest = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["interview", "submitted_by"], name="unique_interview_scorecard")
        ]


class ScorecardAnswer(UUIDTimeStampedModel):
    submission = models.ForeignKey(ScorecardSubmission, on_delete=models.CASCADE, related_name="answers")
    criterion_id = models.UUIDField()
    competency = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField()
    evidence = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["submission", "criterion_id"], name="unique_submission_criterion")
        ]
