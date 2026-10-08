"""The remaining interactive POST handlers in web/views.py (Round 8, part 2).

Round 8 closed the job/candidate/resume/duplicate-merge form handlers. Re-reading the coverage
map showed the *other* half of the interactive surface was still unposted: `interview_create`,
`scorecard` (the submit/draft POST), `scorecard_reopen`, the `profile` password-change branch, and
the error/info branches of `move_stage_htmx`, `reject_htmx` and `interview_complete`. These are the
buttons a reviewer actually clicks on the candidate/interview pages -- "no fake buttons" is not
true until each of them has been posted at least once.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, User
from apps.applications.models import Application, RejectionReason
from apps.interviews.models import Interview, ScorecardSubmission
from apps.interviews.services import save_scorecard, schedule_interview
from apps.jobs.models import (
    Department,
    Job,
    Location,
    PipelineTemplate,
    PipelineTemplateStage,
    StageType,
)

ORG = "northstar-labs"


@pytest.fixture
def admin_user(demo):
    return User.objects.get(email="demo@example.com")


@pytest.fixture
def interviewer(demo):
    """A logged-in client whose role lacks `interviews.manage` and `scorecards.reopen`."""
    user = User.objects.create_user(
        "viewer@example.com", "LongPassword123!", full_name="Vera Viewer"
    )
    OrganizationMembership.objects.create(
        organization=demo,
        user=user,
        role=OrganizationMembership.Role.INTERVIEWER,
        department_scope=[],
        location_scope=[],
    )
    from django.test import Client

    client = Client()
    assert client.login(username="viewer@example.com", password="LongPassword123!")
    return client


def _job_with_scorecard(organization, user):
    """A job built through the real service so it owns a ScorecardTemplate + pipeline stages."""
    from apps.jobs.services import create_job

    department = Department.objects.create(organization=organization, name=f"WS-{uuid.uuid4().hex[:6]}")
    location = Location.objects.create(
        organization=organization,
        name="Remote",
        city="Remote",
        country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    template = PipelineTemplate.objects.create(organization=organization, name=f"WS-{uuid.uuid4().hex[:6]}")
    for position, name in enumerate(("Applied", "Screening"), start=1):
        PipelineTemplateStage.objects.create(
            template=template,
            name=name,
            stage_type=StageType.APPLIED if position == 1 else StageType.SCREENING,
            position=position,
        )
    membership = user.memberships.get(organization=organization)
    return create_job(
        organization=organization,
        actor=user,
        membership=membership,
        data={
            "title": "Engineer",
            "department": department,
            "location": location,
            "hiring_manager": user,
            "recruiter": user,
            "pipeline_template": template,
            "employment_type": Job.EmploymentType.FULL_TIME,
            "headcount": 1,
            "description_html": "<p>x</p>",
        },
    )


def _application(organization, job, stage):
    candidate = __import__("apps.candidates.models", fromlist=["Candidate"]).Candidate.objects.create(
        organization=organization,
        first_name="Wren",
        last_name="Selby",
        city="Remote",
        country_code="ID",
        total_experience_months=12,
        education_level="bachelor",
    )
    candidate.set_email("wren.selby@example.com")
    candidate.save()
    return Application.objects.create(
        organization=organization,
        job=job,
        candidate=candidate,
        current_stage=stage,
        source="MANUAL",
    )


# ------------------------------------------------------------------------- interview_create


@pytest.mark.django_db
def test_interview_create_post_schedules_and_redirects(demo_client, demo, admin_user):
    """The Schedule Interview form writes a real interview and lands on its scorecard."""
    job = _job_with_scorecard(demo, admin_user)
    stage = job.stages.first()
    application = _application(demo, job, stage)
    template = job.scorecard_templates.first()
    before = Interview.objects.count()

    response = demo_client.post(
        reverse("interview-create", args=[ORG, application.id]),
        {
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": "2026-10-01T10:00",
            "end_at": "2026-10-01T11:00",
            "timezone": "UTC",
            "location": "",
            "meeting_url": "",
            "scorecard_template": str(template.id),
            "participants": [str(admin_user.id)],
        },
    )

    assert response.status_code == 302
    assert Interview.objects.count() == before + 1
    interview = Interview.objects.latest("created_at")
    assert interview.participants.filter(user=admin_user).exists()
    assert response.url == reverse("scorecard", args=[ORG, interview.id])


@pytest.mark.django_db
def test_interview_create_post_redisplays_on_invalid_times(demo_client, demo, admin_user):
    """End-before-start must re-render with an error, not create a half-built interview."""
    job = _job_with_scorecard(demo, admin_user)
    stage = job.stages.first()
    application = _application(demo, job, stage)
    template = job.scorecard_templates.first()
    before = Interview.objects.count()

    response = demo_client.post(
        reverse("interview-create", args=[ORG, application.id]),
        {
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": "2026-10-01T11:00",
            "end_at": "2026-10-01T10:00",  # before start
            "timezone": "UTC",
            "scorecard_template": str(template.id),
            "participants": [str(admin_user.id)],
        },
    )

    assert response.status_code == 200
    assert Interview.objects.count() == before
    assert b"after start" in response.content


@pytest.mark.django_db
def test_interview_create_refuses_without_interviews_manage(demo_client, demo, admin_user, interviewer):
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())

    response = interviewer.post(
        reverse("interview-create", args=[ORG, application.id]),
        {"title": "x", "interview_type": "VIDEO", "start_at": "2026-10-01T10:00",
         "end_at": "2026-10-01T11:00", "timezone": "UTC"},
    )

    assert response.status_code == 404
    # The denied request must not have created an interview.
    assert Interview.objects.filter(application=application).count() == 0


# --------------------------------------------------------------------------------- scorecard


@pytest.mark.django_db
def test_scorecard_submit_locks_the_submission(demo_client, demo, admin_user):
    """Submitting as an assigned panellist persists ratings and locks the scorecard."""
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    interview = schedule_interview(
        application=application,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": timezone.now(),
            "end_at": timezone.now() + timedelta(hours=1),
            "timezone": "UTC",
            "scorecard_template": job.scorecard_templates.first(),
        },
        participants=[admin_user],
    )

    criteria = interview.template_snapshot["criteria"]
    payload = {"recommendation": "YES", "action": "submit"}
    for criterion in criteria:
        payload[f"rating_{criterion['id']}"] = "4"
        payload[f"evidence_{criterion['id']}"] = "Solid signal."

    response = demo_client.post(reverse("scorecard", args=[ORG, interview.id]), payload)

    assert response.status_code == 302
    submission = ScorecardSubmission.objects.get(interview=interview, submitted_by=admin_user)
    assert submission.status == ScorecardSubmission.Status.SUBMITTED
    assert submission.locked_at is not None
    assert submission.answers.count() == len(criteria)


@pytest.mark.django_db
def test_scorecard_submit_refuses_a_non_participant(demo_client, demo, admin_user, interviewer):
    """A viewer who is not on the panel gets a 404 -- the submit branch must not run."""
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    interview = schedule_interview(
        application=application,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": timezone.now(),
            "end_at": timezone.now() + timedelta(hours=1),
            "timezone": "UTC",
            "scorecard_template": job.scorecard_templates.first(),
        },
        participants=[admin_user],
    )

    response = interviewer.post(
        reverse("scorecard", args=[ORG, interview.id]),
        {"recommendation": "YES", "action": "submit", "rating_x": "4"},
    )

    assert response.status_code == 404
    assert not ScorecardSubmission.objects.filter(interview=interview).exists()


# ----------------------------------------------------------------------------- scorecard_reopen


@pytest.mark.django_db
def test_scorecard_reopen_unlocks_a_submitted_scorecard(demo_client, demo, admin_user):
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    interview = schedule_interview(
        application=application,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": timezone.now(),
            "end_at": timezone.now() + timedelta(hours=1),
            "timezone": "UTC",
            "scorecard_template": job.scorecard_templates.first(),
        },
        participants=[admin_user],
    )
    criteria = interview.template_snapshot["criteria"]
    answers = {c["id"]: {"rating": 4, "evidence": "ok"} for c in criteria}
    save_scorecard(
        interview=interview, actor=admin_user, recommendation="YES", summary="x",
        answers=answers, submit=True,
    )

    response = demo_client.post(
        reverse("scorecard-reopen", args=[ORG, interview.id, interview.scorecard_submissions.first().id]),
        {"reason": "Re-evaluating after new context."},
    )

    assert response.status_code == 302
    submission = interview.scorecard_submissions.first()
    assert submission.status == ScorecardSubmission.Status.REOPENED
    assert submission.locked_at is None


@pytest.mark.django_db
def test_scorecard_reopen_handles_an_unknown_submission(demo_client, demo, admin_user):
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    interview = schedule_interview(
        application=application,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": timezone.now(),
            "end_at": timezone.now() + timedelta(hours=1),
            "timezone": "UTC",
            "scorecard_template": job.scorecard_templates.first(),
        },
        participants=[admin_user],
    )

    response = demo_client.post(
        reverse("scorecard-reopen", args=[ORG, interview.id, uuid.uuid4()]),
        {"reason": "x"},
        follow=True,
    )

    assert response.status_code == 200
    assert b"not found" in response.content.lower()


# ------------------------------------------------------------------------------ profile (password)


@pytest.mark.django_db
def test_profile_password_change_updates_credentials(demo_client, demo, admin_user):
    from django.test import Client

    response = demo_client.post(
        reverse("profile", args=[ORG]),
        {
            "action": "password",
            "old_password": "demo123",
            "new_password1": "NewPass123!",
            "new_password2": "NewPass123!",
        },
        follow=True,
    )

    assert b"Password updated." in response.content
    # The new password must actually work on a fresh session.
    fresh = Client()
    assert fresh.login(username="demo@example.com", password="NewPass123!")


# ----------------------------------------------------------------------- interview_complete (info)


@pytest.mark.django_db
def test_interview_complete_reports_when_already_completed(demo_client, demo, admin_user):
    """Completing twice is idempotent: the second POST is an info message, not an error."""
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    interview = schedule_interview(
        application=application,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Tech screen",
            "interview_type": "VIDEO",
            "start_at": timezone.now(),
            "end_at": timezone.now() + timedelta(hours=1),
            "timezone": "UTC",
            "scorecard_template": job.scorecard_templates.first(),
        },
        participants=[admin_user],
    )
    demo_client.post(reverse("interview-complete", args=[ORG, interview.id]), follow=True)

    response = demo_client.post(reverse("interview-complete", args=[ORG, interview.id]), follow=True)

    assert b"already completed" in response.content.lower()
    interview.refresh_from_db()
    assert interview.status == Interview.Status.COMPLETED


# --------------------------------------------------------- move_stage_htmx / reject_htmx error branches


@pytest.mark.django_db
def test_move_stage_htmx_returns_409_when_interview_required(demo_client, demo, admin_user):
    """A stage gated on an interview must refuse the move with a 409, not a 500 or silent no-op."""
    job = _job_with_scorecard(demo, admin_user)
    first, second = list(job.stages.all())
    second.requires_interview = True
    second.save(update_fields=["requires_interview"])
    application = _application(demo, job, first)

    response = demo_client.post(
        reverse("move-stage", args=[ORG, application.id]),
        {
            "target_stage_id": str(second.id),
            "expected_version": application.version,
            "idempotency_key": "interview-gate",
        },
    )

    assert response.status_code == 409
    application.refresh_from_db()
    assert application.current_stage_id == first.id


@pytest.mark.django_db
def test_reject_htmx_shows_error_when_already_rejected(demo_client, demo, admin_user):
    """Rejecting an already-rejected application must surface a message, not crash or silently pass."""
    job = _job_with_scorecard(demo, admin_user)
    application = _application(demo, job, job.stages.first())
    reason = RejectionReason.objects.filter(organization=demo, active=True).first()
    assert reason is not None
    demo_client.post(
        reverse("reject-application", args=[ORG, application.id]),
        {"reason": str(reason.id), "note": "First pass."},
        follow=True,
    )
    application.refresh_from_db()
    assert application.status == Application.Status.REJECTED

    response = demo_client.post(
        reverse("reject-application", args=[ORG, application.id]),
        {"reason": str(reason.id), "note": "Second pass."},
        follow=True,
    )

    assert b"Only active applications" in response.content
    # Count of rejects is unchanged -- the second call was refused, not applied twice.
    assert Application.objects.filter(status=Application.Status.REJECTED, id=application.id).count() == 1
