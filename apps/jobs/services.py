from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.accounts.policies import require_permission, scope_jobs
from apps.audit.models import record_audit

from .models import Job, PipelineStage, ScorecardCriterion, ScorecardTemplate

ALLOWED_TRANSITIONS = {
    Job.Status.DRAFT: {Job.Status.PENDING_APPROVAL, Job.Status.OPEN},
    Job.Status.PENDING_APPROVAL: {Job.Status.DRAFT, Job.Status.OPEN},
    Job.Status.OPEN: {Job.Status.PAUSED, Job.Status.CLOSED},
    Job.Status.PAUSED: {Job.Status.OPEN, Job.Status.CLOSED},
    Job.Status.CLOSED: {Job.Status.ARCHIVED},
    Job.Status.ARCHIVED: set(),
}


@transaction.atomic
def create_job(*, organization, actor, membership, data, skill_requirements=()):
    require_permission(membership, "jobs.manage")
    template = data["pipeline_template"]
    if template.organization_id != organization.id:
        raise ValidationError("Invalid pipeline template.")
    job = Job.objects.create(organization=organization, **data)
    PipelineStage.objects.bulk_create(
        [
            PipelineStage(
                job=job,
                name=stage.name,
                stage_type=stage.stage_type,
                position=stage.position,
                sla_hours=stage.sla_hours,
                requires_interview=stage.requires_interview,
                requires_scorecard=stage.requires_scorecard,
                terminal=stage.terminal,
            )
            for stage in template.stages.all()
        ]
    )
    scorecard = ScorecardTemplate.objects.create(job=job, name="Default interview scorecard")
    ScorecardCriterion.objects.bulk_create(
        [
            ScorecardCriterion(template=scorecard, competency="Role expertise", position=1, weight=1),
            ScorecardCriterion(template=scorecard, competency="Problem solving", position=2, weight=1),
            ScorecardCriterion(template=scorecard, competency="Communication", position=3, weight=1),
        ]
    )
    for requirement in skill_requirements:
        requirement.job = job
        requirement.save()
    record_audit(organization=organization, actor=actor, action="job.created", resource=job)
    return job


@transaction.atomic
def update_job(*, job_id, organization, actor, membership, data, request=None):
    """Apply an edit to a job. Publishing stays in ``transition_job`` so approval rules hold."""
    require_permission(membership, "jobs.manage")
    job = scope_jobs(
        membership, Job.objects.select_for_update().filter(deleted_at__isnull=True)
    ).filter(id=job_id).first()
    if not job:
        raise ValidationError("Job not found.")
    if job.organization_id != organization.id:
        raise ValidationError("Job not found.")
    before = {field: str(getattr(job, field)) for field in data}
    for field, value in data.items():
        setattr(job, field, value)
    job.version += 1
    job.save()
    record_audit(
        organization=organization,
        actor=actor,
        action="job.updated",
        resource=job,
        before=before,
        after={field: str(getattr(job, field)) for field in data},
        request=request,
    )
    return job


@transaction.atomic
def transition_job(*, job_id, organization, actor, membership, target_status, expected_version=None, request=None):
    require_permission(membership, "jobs.publish")
    job = scope_jobs(membership, Job.objects.select_for_update()).filter(id=job_id).first()
    if not job:
        raise ValidationError("Job not found.")
    if expected_version is not None and job.version != expected_version:
        raise ValidationError("This job was updated by another user. Refresh and retry.")
    if target_status not in ALLOWED_TRANSITIONS[job.status]:
        raise ValidationError(f"Cannot change job from {job.status} to {target_status}.")
    if target_status == Job.Status.OPEN and organization.job_approval_required:
        if membership.role not in {OrganizationMembership.Role.ORG_ADMIN, OrganizationMembership.Role.HR_ADMIN}:
            raise ValidationError("An HR or organization administrator must approve this job.")
        job.approved_by = actor
        job.approved_at = timezone.now()
    before = {"status": job.status, "version": job.version}
    job.status = target_status
    job.version += 1
    if target_status == Job.Status.OPEN:
        job.published_at = timezone.now()
    if target_status == Job.Status.CLOSED:
        job.closed_at = timezone.now()
    job.save()
    record_audit(
        organization=organization,
        actor=actor,
        action="job.status_changed",
        resource=job,
        before=before,
        after={"status": job.status, "version": job.version},
        request=request,
    )
    return job
