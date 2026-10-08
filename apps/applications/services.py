from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.accounts.policies import require_permission, scope_applications
from apps.audit.models import record_audit
from apps.candidates.matching import FORMULA_VERSION, calculate_match
from apps.jobs.models import StageType

from .models import Application, ApplicationStageHistory, IdempotencyRecord, MatchScore


def _check_job_scope(membership, job):
    if membership.role == OrganizationMembership.Role.HIRING_MANAGER:
        if job.hiring_manager_id != membership.user_id and not job.team_members.filter(
            user_id=membership.user_id
        ).exists():
            raise ValidationError("You do not have access to this job.")
    if membership.role == OrganizationMembership.Role.RECRUITER:
        if membership.department_scope and str(job.department_id) not in membership.department_scope:
            raise ValidationError("This job is outside your department scope.")
        if membership.location_scope and str(job.location_id) not in membership.location_scope:
            raise ValidationError("This job is outside your location scope.")


@transaction.atomic
def move_application_stage(
    *, application_id, target_stage_id, organization, actor, membership, idempotency_key, expected_version, reason="", request=None
):
    require_permission(membership, "applications.move")
    if not idempotency_key:
        raise ValidationError("Idempotency-Key is required.")
    existing = IdempotencyRecord.objects.filter(
        organization=organization, action="application.move_stage", key=idempotency_key
    ).first()
    if existing:
        application = scope_applications(membership).filter(id=existing.resource_id).first()
        if not application:
            raise ValidationError("Application not found.")
        return application
    application = scope_applications(
        membership,
        Application.objects.select_for_update().select_related("job", "current_stage", "candidate"),
    ).filter(id=application_id).first()
    if not application:
        raise ValidationError("Application not found.")
    _check_job_scope(membership, application.job)
    if application.version != expected_version:
        raise ValidationError("This application changed. Refresh the pipeline and try again.")
    if application.status != Application.Status.ACTIVE:
        raise ValidationError("Only active applications can move between stages.")
    target = application.job.stages.filter(id=target_stage_id, active=True).first()
    if not target:
        raise ValidationError("The target stage does not belong to this job.")
    if target.id == application.current_stage_id:
        raise ValidationError("The application is already in this stage.")
    if target.requires_interview and not application.interviews.exclude(status="CANCELLED").exists():
        raise ValidationError("Schedule an interview before entering this stage.")
    if target.requires_scorecard and not application.scorecard_submissions.filter(status="SUBMITTED").exists():
        raise ValidationError("Submit the required scorecard before entering this stage.")
    now = timezone.now()
    previous = application.current_stage
    duration = max(int((now - application.last_stage_changed_at).total_seconds()), 0)
    application.current_stage = target
    application.last_stage_changed_at = now
    application.version += 1
    if target.stage_type == StageType.HIRED:
        application.status = Application.Status.HIRED
        application.hired_at = now
    application.save()
    ApplicationStageHistory.objects.create(
        application=application,
        from_stage=previous,
        to_stage=target,
        moved_by=actor,
        reason=reason,
        duration_previous_stage_seconds=duration,
    )
    IdempotencyRecord.objects.create(
        organization=organization,
        key=idempotency_key,
        action="application.move_stage",
        resource_type="applications.application",
        resource_id=str(application.id),
        response={"stage_id": str(target.id), "version": application.version},
    )
    record_audit(
        organization=organization,
        actor=actor,
        action="application.stage_moved",
        resource=application,
        before={"stage_id": str(previous.id), "version": expected_version},
        after={"stage_id": str(target.id), "version": application.version},
        request=request,
    )
    return application


@transaction.atomic
def reject_application(*, application_id, reason, organization, actor, membership, note="", request=None):
    require_permission(membership, "applications.reject")
    application = scope_applications(
        membership,
        Application.objects.select_for_update().select_related("job"),
    ).filter(id=application_id).first()
    if not application:
        raise ValidationError("Application not found.")
    _check_job_scope(membership, application.job)
    if application.status != Application.Status.ACTIVE:
        raise ValidationError("Only active applications can be rejected.")
    application.status = Application.Status.REJECTED
    application.rejection_reason = reason
    application.rejected_at = timezone.now()
    application.version += 1
    application.save()
    record_audit(
        organization=organization,
        actor=actor,
        action="application.rejected",
        resource=application,
        after={"reason": reason.name, "note": note},
        request=request,
    )
    return application


def recompute_match_score(application):
    total, components, explanation = calculate_match(application)
    return MatchScore.objects.update_or_create(
        application=application,
        defaults={
            "model_version": FORMULA_VERSION,
            "total_score": total,
            "skill_score": components.get("skills"),
            "experience_score": components.get("experience"),
            "education_score": components.get("education"),
            "location_score": components.get("location"),
            "explanation": explanation,
        },
    )[0]
