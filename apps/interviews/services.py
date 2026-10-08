from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.accounts.policies import require_permission, scope_applications, scope_interviews
from apps.audit.models import record_audit

from .models import Interview, InterviewParticipant, ScorecardAnswer, ScorecardSubmission


@transaction.atomic
def schedule_interview(*, application, actor, membership, data, participants, request=None):
    require_permission(membership, "interviews.manage")
    if not scope_applications(membership).filter(id=application.id).exists():
        raise ValidationError("Invalid application.")
    template = data["scorecard_template"]
    if template.job_id != application.job_id:
        raise ValidationError("The scorecard does not belong to this job.")
    snapshot = {
        "name": template.name,
        "version": template.version,
        "criteria": [
            {
                "id": str(row.id),
                "competency": row.competency,
                "description": row.description,
                "weight": str(row.weight),
                "position": row.position,
            }
            for row in template.criteria.all()
        ],
    }
    interview = Interview.objects.create(
        application=application,
        created_by=actor,
        template_snapshot=snapshot,
        **data,
    )
    InterviewParticipant.objects.bulk_create(
        [InterviewParticipant(interview=interview, user=user) for user in participants]
    )
    record_audit(
        organization=application.organization,
        actor=actor,
        action="interview.scheduled",
        resource=interview,
        after={"participant_ids": [str(user.id) for user in participants]},
        request=request,
    )
    transaction.on_commit(lambda: _enqueue_invites(interview.id))
    return interview


def _enqueue_invites(interview_id):
    from .tasks import send_interview_invites

    send_interview_invites.delay(str(interview_id))


@transaction.atomic
def set_interview_status(*, interview_id, organization, actor, membership, status, request=None):
    """Move an interview to cancelled or completed. Idempotent for the same target status."""
    require_permission(membership, "interviews.manage")
    if status not in {Interview.Status.CANCELLED, Interview.Status.COMPLETED}:
        raise ValidationError("An interview can only be cancelled or completed.")
    interview = (
        Interview.objects.select_for_update()
        .select_related("application")
        .filter(id=interview_id, application__organization=organization)
        .first()
    )
    if not interview:
        raise ValidationError("Interview not found.")
    if not scope_interviews(membership).filter(id=interview.id).exists():
        raise ValidationError("Interview not found.")
    if interview.status == status:
        return interview, False
    before = {"status": interview.status}
    interview.status = status
    interview.save(update_fields=["status", "updated_at"])
    record_audit(
        organization=organization,
        actor=actor,
        action=(
            "interview.cancelled" if status == Interview.Status.CANCELLED else "interview.completed"
        ),
        resource=interview,
        before=before,
        after={"status": interview.status},
        request=request,
    )
    return interview, True


@transaction.atomic
def save_scorecard(*, interview, actor, recommendation, summary, answers, submit=False, conflict=False):
    membership = OrganizationMembership.objects.filter(
        organization=interview.application.organization,
        user=actor,
        active=True,
    ).first()
    if not membership:
        raise ValidationError("You do not have access to this organization.")
    require_permission(membership, "scorecards.submit")
    if not interview.participants.filter(user=actor).exists():
        raise ValidationError("You are not assigned to this interview.")
    valid_recommendations = {value for value, _ in ScorecardSubmission.Recommendation.choices}
    if recommendation and recommendation not in valid_recommendations:
        raise ValidationError("Select a valid overall recommendation.")
    submission, _ = ScorecardSubmission.objects.select_for_update().get_or_create(
        interview=interview,
        submitted_by=actor,
        defaults={
            "application": interview.application,
            "template_version": interview.template_snapshot["version"],
        },
    )
    if submission.status == ScorecardSubmission.Status.SUBMITTED:
        raise ValidationError("Submitted scorecards are locked.")
    criteria = {row["id"]: row for row in interview.template_snapshot.get("criteria", [])}
    if submit and set(answers) != set(criteria):
        raise ValidationError("Rate every scorecard criterion before submitting.")
    for criterion_id, answer in answers.items():
        if criterion_id not in criteria:
            raise ValidationError("Unknown scorecard criterion.")
        try:
            rating = int(answer.get("rating", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Ratings must be between 1 and 5.") from exc
        evidence = str(answer.get("evidence", "")).strip()
        if rating not in range(1, 6):
            raise ValidationError("Ratings must be between 1 and 5.")
        if rating in {1, 5} and not evidence:
            raise ValidationError("Evidence is required for extreme ratings.")
        ScorecardAnswer.objects.update_or_create(
            submission=submission,
            criterion_id=criterion_id,
            defaults={
                "competency": criteria[criterion_id]["competency"],
                "rating": rating,
                "evidence": evidence,
            },
        )
    submission.overall_recommendation = recommendation
    submission.summary = summary
    submission.conflict_of_interest = conflict
    if submit:
        if not recommendation:
            raise ValidationError("Overall recommendation is required.")
        submission.status = ScorecardSubmission.Status.SUBMITTED
        submission.submitted_at = timezone.now()
        submission.locked_at = timezone.now()
    else:
        submission.status = ScorecardSubmission.Status.DRAFT
    submission.save()
    record_audit(
        organization=interview.application.organization,
        actor=actor,
        action="scorecard.submitted" if submit else "scorecard.saved",
        resource=submission,
    )
    return submission


@transaction.atomic
def reopen_scorecard(*, submission_id, organization, actor, membership, reason, interview_id=None):
    if membership.role not in {OrganizationMembership.Role.ORG_ADMIN, OrganizationMembership.Role.HR_ADMIN}:
        raise ValidationError("Only an HR or organization administrator can reopen a scorecard.")
    if not reason.strip():
        raise ValidationError("A reason is required.")
    queryset = ScorecardSubmission.objects.select_for_update().filter(
        id=submission_id, application__organization=organization
    )
    # The view already catches ValidationError, so a stale or mistyped submission id must
    # surface as one -- not as the DoesNotExist that `.get()` raised, which was uncaught and
    # turned a dead link into an HTTP 500 for the very administrator allowed to act on it.
    if interview_id is not None:
        queryset = queryset.filter(interview_id=interview_id)
    submission = queryset.first()
    if not submission:
        raise ValidationError("Scorecard not found.")
    submission.status = ScorecardSubmission.Status.REOPENED
    submission.locked_at = None
    submission.save(update_fields=["status", "locked_at", "updated_at"])
    record_audit(
        organization=organization,
        actor=actor,
        action="scorecard.reopened",
        resource=submission,
        after={"reason": reason},
    )
    return submission
