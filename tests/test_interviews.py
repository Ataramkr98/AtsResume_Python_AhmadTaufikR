"""Interview service coverage.

`schedule_interview` and `reopen_scorecard` had never been executed by a test. The latter
turned out to raise an uncaught `ScorecardSubmission.DoesNotExist` on a stale or mistyped
submission id -- the view only catches `ValidationError`, so an administrator hitting a dead
link got a 500 instead of a message. That is the kind of bug that lives forever in an
untested branch, because 500s on admin-only paths are rarely seen by anyone but the admin.

The tests also pin down the guard rails in `schedule_interview`, `set_interview_status` and
`save_scorecard` that were previously only asserted in prose.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, User
from apps.applications.models import Application
from apps.audit.models import AuditLog
from apps.interviews.models import (
    Interview,
    InterviewParticipant,
    ScorecardSubmission,
)
from apps.interviews.services import (
    reopen_scorecard,
    save_scorecard,
    schedule_interview,
    set_interview_status,
)
from apps.jobs.models import ScorecardCriterion, ScorecardTemplate

ORG = "northstar-labs"


def make_member(organization, email, *, role):
    user = User.objects.create_user(email, "LongPassword123!", full_name="Interviewer")
    membership = OrganizationMembership.objects.create(
        organization=organization, user=user, role=role
    )
    return user, membership


@pytest.fixture
def seeded_interview(demo):
    """A scheduled interview in the demo workspace, with its participants and template."""
    return (
        Interview.objects.filter(application__organization=demo)
        .select_related("application__job")
        .prefetch_related("participants__user")
        .first()
    )


@pytest.fixture
def admin(demo):
    user = User.objects.get(email="demo@example.com")
    membership = OrganizationMembership.objects.get(organization=demo, user=user)
    return user, membership


@pytest.fixture
def scorecard_job(demo, admin):
    """A fresh job in the demo workspace.

    A `ScorecardTemplate` is unique per (job, version), and the seeder already owns version 1
    of the demo job's template -- so every test that needs its own template gets its own job
    instead of fighting that constraint.
    """
    from apps.jobs.models import Department, Job, Location, PipelineTemplate

    user, _ = admin
    department = Department.objects.create(organization=demo, name="Scoring")
    location = Location.objects.create(
        organization=demo, name="Remote", city="Remote", country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    pipeline = PipelineTemplate.objects.create(organization=demo, name="Scoring")
    return Job.objects.create(
        organization=demo, department=department, location=location,
        hiring_manager=user, recruiter=user, pipeline_template=pipeline,
        title="Scoring role", slug=uuid.uuid4().hex[:12], status=Job.Status.OPEN,
        employment_type=Job.EmploymentType.FULL_TIME, description_html="<p>x</p>",
        min_experience_months=0, education_level="bachelor",
    )


@pytest.fixture
def submission(demo, seeded_interview, admin):
    """A submitted scorecard on the seeded interview, owned by the admin."""
    user, _ = admin
    now = timezone.now()
    return ScorecardSubmission.objects.create(
        interview=seeded_interview,
        application=seeded_interview.application,
        submitted_by=user,
        template_version=1,
        status=ScorecardSubmission.Status.SUBMITTED,
        submitted_at=now,
        locked_at=now,
    )


# ------------------------------------------------------------------ schedule_interview


@pytest.mark.django_db
def test_schedule_interview_snapshots_the_template_and_creates_participants(demo, admin, seeded_interview, scorecard_job):
    """A scheduled interview records the template it was scored against, for a stable audit."""
    user, membership = admin
    application = seeded_interview.application
    template = ScorecardTemplate.objects.create(job=application.job, name="Screening", version=2)
    criterion = ScorecardCriterion.objects.create(
        template=template, competency="Communication", position=1
    )
    panellist, _ = make_member(demo, "panellist@example.com", role=OrganizationMembership.Role.RECRUITER)
    participant_user, _ = make_member(
        demo, "interviewee@example.com", role=OrganizationMembership.Role.INTERVIEWER
    )

    interview = schedule_interview(
        application=application,
        actor=user,
        membership=membership,
        data={
            "scorecard_template": template,
            "title": "Screening call",
            "start_at": timezone.now() + timedelta(days=1),
            "end_at": timezone.now() + timedelta(days=1, hours=1),
            "interview_type": "VIDEO",
            "timezone": "Asia/Jakarta",
        },
        participants=[panellist, participant_user],
    )

    assert interview.template_snapshot["criteria"][0]["id"] == str(criterion.id)
    assert set(interview.participants.values_list("user_id", flat=True)) == {
        panellist.id,
        participant_user.id,
    }
    assert AuditLog.objects.filter(
        action="interview.scheduled", resource_id=str(interview.pk)
    ).exists()


@pytest.mark.django_db
def test_schedule_interview_rejects_a_template_from_another_job(demo, admin, seeded_interview, scorecard_job):
    """Otherwise the scorecard would be scored against the wrong role's rubric."""
    user, membership = admin
    application = seeded_interview.application
    foreign_template = _make_foreign_template()

    with pytest.raises(ValidationError, match="does not belong to this job"):
        schedule_interview(
            application=application,
            actor=user,
            membership=membership,
            data={
                "scorecard_template": foreign_template,
                "title": "Bad",
                "start_at": timezone.now() + timedelta(days=1),
                "end_at": timezone.now() + timedelta(days=1, hours=1),
                "interview_type": "VIDEO",
                "timezone": "UTC",
            },
            participants=[user],
        )


def _make_foreign_template():
    """A scorecard template attached to a job in a throwaway organization."""
    from apps.accounts.models import Organization
    from apps.jobs.models import Department, Job, Location, PipelineTemplate

    org = Organization.objects.create(name="Foreign Co", slug=uuid.uuid4().hex[:12])
    department = Department.objects.create(organization=org, name="X")
    location = Location.objects.create(
        organization=org, name="Remote", city="Remote", country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    pipeline = PipelineTemplate.objects.create(organization=org, name="Default")
    job = Job.objects.create(
        organization=org, department=department, location=location,
        hiring_manager=User.objects.create_user("f@example.com", "LongPassword123!", full_name="F"),
        recruiter=User.objects.create_user("f2@example.com", "LongPassword123!", full_name="F2"),
        pipeline_template=pipeline, title="Foreign role", slug="foreign-role",
        status=Job.Status.OPEN, employment_type=Job.EmploymentType.FULL_TIME,
        description_html="<p>x</p>", min_experience_months=0, education_level="bachelor",
    )
    return ScorecardTemplate.objects.create(job=job, name="Foreign", version=1)


def _make_foreign_interview(user):
    """An interview that lives in a different organization from the demo workspace."""
    from apps.accounts.models import Organization
    from apps.candidates.models import Candidate
    from apps.jobs.models import (
        Department,
        Job,
        Location,
        PipelineStage,
        PipelineTemplate,
        StageType,
    )

    org = Organization.objects.create(name="Other Co", slug=uuid.uuid4().hex[:12])
    department = Department.objects.create(organization=org, name="X")
    location = Location.objects.create(
        organization=org, name="Remote", city="Remote", country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    pipeline = PipelineTemplate.objects.create(organization=org, name="Default")
    job = Job.objects.create(
        organization=org, department=department, location=location,
        hiring_manager=user, recruiter=user, pipeline_template=pipeline,
        title="Foreign", slug="foreign", status=Job.Status.OPEN,
        employment_type=Job.EmploymentType.FULL_TIME, description_html="<p>x</p>",
        min_experience_months=0, education_level="bachelor",
    )
    stage = PipelineStage.objects.create(
        job=job, name="Applied", stage_type=StageType.APPLIED, position=1
    )
    candidate = Candidate.objects.create(
        organization=org, first_name="F", last_name="X", city="Remote",
        country_code="ID", total_experience_months=0, education_level="bachelor",
    )
    candidate.set_email("foreign@example.com")
    application = Application.objects.create(
        organization=org, job=job, candidate=candidate, current_stage=stage, source="MANUAL",
    )
    template = ScorecardTemplate.objects.create(job=job, name="T", version=1)
    interview = Interview.objects.create(
        application=application, scorecard_template=template,
        template_snapshot={"version": 1, "criteria": []}, title="Foreign",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1), created_by=user,
    )
    return interview


@pytest.mark.django_db
def test_schedule_interview_requires_the_manage_permission(demo, seeded_interview, scorecard_job):
    """An interviewer can score, but must not be able to schedule new interviews."""
    user, membership = make_member(
        demo, "plain.interviewer@example.com", role=OrganizationMembership.Role.INTERVIEWER
    )

    with pytest.raises(Http404):
        schedule_interview(
            application=seeded_interview.application,
            actor=user,
            membership=membership,
            data={
                "scorecard_template": ScorecardTemplate.objects.create(
                    job=scorecard_job, name="T", version=1
                ),
                "title": "Nope",
                "start_at": timezone.now() + timedelta(days=1),
                "end_at": timezone.now() + timedelta(days=1, hours=1),
                "interview_type": "VIDEO",
                "timezone": "UTC",
            },
            participants=[user],
        )


# ------------------------------------------------------------------ set_interview_status


@pytest.mark.django_db
def test_set_interview_status_cancels_and_audits(demo, admin, seeded_interview, scorecard_job):
    user, membership = admin

    interview, changed = set_interview_status(
        interview_id=seeded_interview.id,
        organization=demo,
        actor=user,
        membership=membership,
        status=Interview.Status.CANCELLED,
    )

    assert changed is True
    assert interview.status == Interview.Status.CANCELLED
    assert AuditLog.objects.filter(
        action="interview.cancelled", resource_id=str(interview.pk)
    ).exists()


@pytest.mark.django_db
def test_set_interview_status_is_idempotent(demo, admin, seeded_interview, scorecard_job):
    user, membership = admin
    set_interview_status(
        interview_id=seeded_interview.id, organization=demo, actor=user,
        membership=membership, status=Interview.Status.COMPLETED,
    )

    _, changed_again = set_interview_status(
        interview_id=seeded_interview.id, organization=demo, actor=user,
        membership=membership, status=Interview.Status.COMPLETED,
    )

    assert changed_again is False


@pytest.mark.django_db
def test_set_interview_status_rejects_an_invalid_target(demo, admin, seeded_interview, scorecard_job):
    user, membership = admin

    with pytest.raises(ValidationError, match="only be cancelled or completed"):
        set_interview_status(
            interview_id=seeded_interview.id, organization=demo, actor=user,
            membership=membership, status="SCHEDULED",
        )


@pytest.mark.django_db
def test_set_interview_status_cannot_reach_another_workspaces_interview(demo, admin):
    """The org-scoped lookup is the boundary; an unknown id must read as missing."""
    user, membership = admin
    foreign_interview = _make_foreign_interview(user)

    with pytest.raises(ValidationError, match="Interview not found"):
        set_interview_status(
            interview_id=foreign_interview.id, organization=demo, actor=user,
            membership=membership, status=Interview.Status.CANCELLED,
        )


# ----------------------------------------------------------------------- save_scorecard


@pytest.mark.django_db
def test_save_scorecard_requires_participation(demo, admin, seeded_interview, scorecard_job):
    """A user who is not on the panel must not be able to file a scorecard."""
    user, _ = admin
    interloper, _ = make_member(
        demo, "not-on-panel@example.com", role=OrganizationMembership.Role.RECRUITER
    )

    template = ScorecardTemplate.objects.create(job=scorecard_job, name="T", version=1)
    criterion = ScorecardCriterion.objects.create(template=template, competency="Skill", position=1)
    interview = Interview.objects.create(
        application=seeded_interview.application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": [{"id": str(criterion.id), "competency": "Skill", "position": 1}]},
        title="Panel",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=user,
    )
    # Deliberately *not* adding `interloper` as a participant.

    with pytest.raises(ValidationError, match="not assigned to this interview"):
        save_scorecard(
            interview=interview, actor=interloper, recommendation="YES", summary="",
            answers={str(criterion.id): {"rating": 3, "evidence": "Fine"}}, submit=True,
        )


@pytest.mark.django_db
def test_save_scorecard_requires_every_criterion_on_submit(demo, admin, seeded_interview, scorecard_job):
    """Submitting with a missing rating must be refused, or the scorecard is incomplete."""
    user, membership = admin
    template = ScorecardTemplate.objects.create(job=scorecard_job, name="T", version=1)
    c1 = ScorecardCriterion.objects.create(template=template, competency="A", position=1)
    c2 = ScorecardCriterion.objects.create(template=template, competency="B", position=2)
    interview = Interview.objects.create(
        application=seeded_interview.application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": [
            {"id": str(c1.id), "competency": "A", "position": 1},
            {"id": str(c2.id), "competency": "B", "position": 2},
        ]},
        title="Panel",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=user,
    )
    InterviewParticipant.objects.create(interview=interview, user=user)

    with pytest.raises(ValidationError, match="Rate every scorecard criterion"):
        save_scorecard(
            interview=interview, actor=user, recommendation="YES", summary="",
            answers={str(c1.id): {"rating": 3, "evidence": "ok"}}, submit=True,
        )


@pytest.mark.django_db
def test_save_scorecard_rejects_an_unknown_criterion(demo, admin, seeded_interview, scorecard_job):
    """On submit the 'rate every criterion' check fires first; a stray criterion id is only
    reachable on a draft save, where the unknown-criterion guard runs."""
    user, membership = admin
    template = ScorecardTemplate.objects.create(job=scorecard_job, name="T", version=1)
    c1 = ScorecardCriterion.objects.create(template=template, competency="A", position=1)
    interview = Interview.objects.create(
        application=seeded_interview.application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": [{"id": str(c1.id), "competency": "A", "position": 1}]},
        title="Panel",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=user,
    )
    InterviewParticipant.objects.create(interview=interview, user=user)

    with pytest.raises(ValidationError, match="Unknown scorecard criterion"):
        save_scorecard(
            interview=interview, actor=user, recommendation="YES", summary="",
            answers={str(uuid.uuid4()): {"rating": 3, "evidence": "ok"}}, submit=False,
        )


@pytest.mark.django_db
def test_save_scorecard_requires_a_recommendation_to_submit(demo, admin, seeded_interview, scorecard_job):
    user, membership = admin
    template = ScorecardTemplate.objects.create(job=scorecard_job, name="T", version=1)
    c1 = ScorecardCriterion.objects.create(template=template, competency="A", position=1)
    interview = Interview.objects.create(
        application=seeded_interview.application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": [{"id": str(c1.id), "competency": "A", "position": 1}]},
        title="Panel",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=user,
    )
    InterviewParticipant.objects.create(interview=interview, user=user)

    with pytest.raises(ValidationError, match="Overall recommendation is required"):
        save_scorecard(
            interview=interview, actor=user, recommendation="", summary="",
            answers={str(c1.id): {"rating": 3, "evidence": "ok"}}, submit=True,
        )


@pytest.mark.django_db
def test_save_scorecard_saves_a_draft_without_submitting(demo, admin, seeded_interview, scorecard_job):
    """A partial save must persist as a DRAFT, not silently become SUBMITTED."""
    user, membership = admin
    template = ScorecardTemplate.objects.create(job=scorecard_job, name="T", version=1)
    c1 = ScorecardCriterion.objects.create(template=template, competency="A", position=1)
    interview = Interview.objects.create(
        application=seeded_interview.application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": [{"id": str(c1.id), "competency": "A", "position": 1}]},
        title="Panel",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=user,
    )
    InterviewParticipant.objects.create(interview=interview, user=user)

    draft = save_scorecard(
        interview=interview, actor=user, recommendation="", summary="working on it",
        answers={}, submit=False,
    )

    assert draft.status == ScorecardSubmission.Status.DRAFT
    assert draft.locked_at is None


# ----------------------------------------------------------------------- reopen_scorecard


@pytest.mark.django_db
def test_reopen_scorecard_requires_an_admin_role(demo, submission, seeded_interview):
    """Only HR/admin may reopen; an interviewer who scored it cannot un-lock it."""
    recruiter, _ = make_member(
        demo, "reopen.requester@example.com", role=OrganizationMembership.Role.RECRUITER
    )

    with pytest.raises(ValidationError, match="Only an HR or organization administrator"):
        reopen_scorecard(
            submission_id=submission.id, organization=demo, actor=recruiter,
            membership=recruiter.memberships.get(organization=demo), reason="Reconsidering",
        )


@pytest.mark.django_db
def test_reopen_scorecard_requires_a_reason(demo, admin, submission):
    user, membership = admin

    with pytest.raises(ValidationError, match="A reason is required"):
        reopen_scorecard(
            submission_id=submission.id, organization=demo, actor=user,
            membership=membership, reason="   ",
        )


@pytest.mark.django_db
def test_reopen_scorecard_unlocks_a_submitted_scorecard(demo, admin, submission):
    user, membership = admin

    reopened = reopen_scorecard(
        submission_id=submission.id, organization=demo, actor=user,
        membership=membership, reason="Second interviewer available.",
    )

    assert reopened.status == ScorecardSubmission.Status.REOPENED
    assert reopened.locked_at is None
    assert AuditLog.objects.filter(
        action="scorecard.reopened", resource_id=str(submission.pk)
    ).exists()


@pytest.mark.django_db
def test_reopen_scorecard_missing_submission_raises_not_500(demo, admin, seeded_interview, scorecard_job):
    """The original code raised DoesNotExist (uncaught -> HTTP 500) on a stale id.

    It must now raise ValidationError, which the view already turns into a redirect and a
    message -- an admin following a dead link gets a friendly error, not a crash.
    """
    user, membership = admin

    with pytest.raises(ValidationError, match="Scorecard not found"):
        reopen_scorecard(
            submission_id=uuid.uuid4(), organization=demo, actor=user,
            membership=membership, reason="Late review",
        )
