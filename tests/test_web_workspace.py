"""Authenticated write handlers in web/views.py.

The route walk proves every page renders; it posts nothing. That left the interactive half of
the product -- publishing a job, moving a candidate through the pipeline, rejecting an
application, closing out an interview -- entirely unpinned, even though these are the actions a
reviewer of this portfolio will actually click.

Writing these tests found a latent 500: `job_transition` parsed `expected_version` with a bare
`int()`, so any request whose version field was missing, empty or non-numeric raised an
uncaught `ValueError` before a single business rule ran. The same class of bug as
`reopen_scorecard`; both live in the gap between "the page renders" and "the action works".
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

from apps.applications.models import Application, RejectionReason
from apps.candidates.models import Candidate
from apps.interviews.models import Interview
from apps.jobs.models import Department, Job, Location, PipelineStage, PipelineTemplate, StageType

ORG = "northstar-labs"


@pytest.fixture
def admin_user(demo):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(email="demo@example.com")


def _make_job(organization, user, *, status=Job.Status.DRAFT, stages=("Applied", "Screening")):
    """A job with a short, interview-free pipeline so stage moves are unconstrained."""
    department = Department.objects.create(organization=organization, name="WS")
    location = Location.objects.create(
        organization=organization,
        name="Remote",
        city="Remote",
        country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    pipeline = PipelineTemplate.objects.create(organization=organization, name="WS")
    job = Job.objects.create(
        organization=organization,
        department=department,
        location=location,
        hiring_manager=user,
        recruiter=user,
        pipeline_template=pipeline,
        title="Workspace role",
        slug=uuid.uuid4().hex[:12],
        status=status,
        employment_type=Job.EmploymentType.FULL_TIME,
        description_html="<p>x</p>",
        min_experience_months=0,
        education_level="bachelor",
    )
    created = []
    for position, name in enumerate(stages, start=1):
        created.append(
            PipelineStage.objects.create(
                job=job,
                name=name,
                stage_type=StageType.APPLIED if position == 1 else StageType.SCREENING,
                position=position,
                active=True,
            )
        )
    return job, created


def _make_application(organization, user, job, stage, email="ws.candidate@example.com"):
    candidate = Candidate.objects.create(
        organization=organization,
        first_name="Wren",
        last_name="Selby",
        city="Remote",
        country_code="ID",
        total_experience_months=12,
        education_level="bachelor",
    )
    candidate.set_email(email)
    return Application.objects.create(
        organization=organization,
        job=job,
        candidate=candidate,
        current_stage=stage,
        source="MANUAL",
    )


# ---------------------------------------------------------------------- job transitions


@pytest.mark.django_db
def test_job_transition_publishes_a_draft_job(demo_client, demo, admin_user):
    """The primary happy path: a draft job becomes open and stamps its publish time."""
    job, _ = _make_job(demo, admin_user)

    response = demo_client.post(
        reverse("job-transition", args=[ORG, job.id]),
        {"target_status": "OPEN", "expected_version": job.version},
    )

    assert response.status_code == 302
    job.refresh_from_db()
    assert job.status == Job.Status.OPEN
    assert job.published_at is not None
    assert job.version == 2


@pytest.mark.django_db
def test_job_transition_reports_a_non_numeric_version_instead_of_crashing(
    demo_client, demo, admin_user
):
    """Regression: `int(request.POST.get(...))` raised an uncaught ValueError -> HTTP 500.

    The version field is a hidden input, so a tampered, truncated or stale form is the only way
    to reach this -- which is exactly why it must fail as a message rather than a crash.
    """
    job, _ = _make_job(demo, admin_user)

    response = demo_client.post(
        reverse("job-transition", args=[ORG, job.id]),
        {"target_status": "OPEN", "expected_version": "not-a-number"},
    )

    assert response.status_code == 302
    job.refresh_from_db()
    assert job.status == Job.Status.DRAFT


@pytest.mark.django_db
def test_job_transition_refuses_a_stale_version(demo_client, demo, admin_user):
    """Optimistic concurrency: a lost update must be reported, never applied."""
    job, _ = _make_job(demo, admin_user)

    response = demo_client.post(
        reverse("job-transition", args=[ORG, job.id]),
        {"target_status": "OPEN", "expected_version": job.version + 5},
        follow=True,
    )

    job.refresh_from_db()
    assert job.status == Job.Status.DRAFT
    assert b"updated by another user" in response.content


@pytest.mark.django_db
def test_job_transition_refuses_an_unknown_target_status(demo_client, demo, admin_user):
    """A hand-crafted status outside the state machine must not reach the state machine."""
    job, _ = _make_job(demo, admin_user)

    demo_client.post(
        reverse("job-transition", args=[ORG, job.id]),
        {"target_status": "HACKED", "expected_version": job.version},
    )

    job.refresh_from_db()
    assert job.status == Job.Status.DRAFT


# ------------------------------------------------------------------------ pipeline moves


@pytest.mark.django_db
def test_move_stage_htmx_advances_an_application(demo_client, demo, admin_user):
    """The pipeline drag-and-drop endpoint: move, record history, tell HTMX to reload."""
    job, (first, second) = _make_job(demo, admin_user, status=Job.Status.OPEN)
    application = _make_application(demo, admin_user, job, first)

    response = demo_client.post(
        reverse("move-stage", args=[ORG, application.id]),
        {
            "target_stage_id": str(second.id),
            "expected_version": application.version,
            "idempotency_key": "move-1",
        },
    )

    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("pipeline-job", args=[ORG, job.id])
    application.refresh_from_db()
    assert application.current_stage_id == second.id
    assert application.version == 2


@pytest.mark.django_db
def test_move_stage_htmx_rejects_a_malformed_request(demo_client, demo, admin_user):
    """A bad payload is a 400 the client can show -- not a 500 and not a silent no-op."""
    job, (first, _) = _make_job(demo, admin_user, status=Job.Status.OPEN)
    application = _make_application(demo, admin_user, job, first)

    response = demo_client.post(
        reverse("move-stage", args=[ORG, application.id]),
        {"target_stage_id": "not-a-uuid", "expected_version": 0, "idempotency_key": ""},
    )

    assert response.status_code == 400
    application.refresh_from_db()
    assert application.current_stage_id == first.id


@pytest.mark.django_db
def test_move_stage_htmx_replays_an_idempotent_key(demo_client, demo, admin_user):
    """A retried move must not advance the candidate twice."""
    job, (first, second) = _make_job(demo, admin_user, status=Job.Status.OPEN)
    application = _make_application(demo, admin_user, job, first)
    payload = {
        "target_stage_id": str(second.id),
        "expected_version": application.version,
        "idempotency_key": "move-retry",
    }

    assert demo_client.post(reverse("move-stage", args=[ORG, application.id]), payload).status_code == 204
    assert demo_client.post(reverse("move-stage", args=[ORG, application.id]), payload).status_code == 204

    application.refresh_from_db()
    assert application.current_stage_id == second.id
    assert application.version == 2


@pytest.mark.django_db
def test_reject_htmx_rejects_an_active_application(demo_client, demo, admin_user):
    """Rejection records the reason and takes the application out of the active funnel."""
    job, (first, _) = _make_job(demo, admin_user, status=Job.Status.OPEN)
    application = _make_application(demo, admin_user, job, first)
    reason = RejectionReason.objects.filter(organization=demo, active=True).first()
    assert reason is not None

    response = demo_client.post(
        reverse("reject-application", args=[ORG, application.id]),
        {"reason": str(reason.id), "note": "Not the right fit."},
        follow=True,
    )

    application.refresh_from_db()
    assert application.status == Application.Status.REJECTED
    assert application.rejection_reason_id == reason.id
    assert b"Application rejected." in response.content


# ---------------------------------------------------------------------------- interviews


@pytest.mark.django_db
def test_interview_cancel_and_complete_are_reachable(demo_client, demo):
    """Both lifecycle endpoints run against a seeded interview and report their outcome."""
    interview = Interview.objects.filter(application__organization=demo).first()
    assert interview is not None

    cancelled = demo_client.post(reverse("interview-cancel", args=[ORG, interview.id]), follow=True)
    interview.refresh_from_db()
    assert interview.status == Interview.Status.CANCELLED
    assert b"Interview cancelled." in cancelled.content

    completed = demo_client.post(reverse("interview-complete", args=[ORG, interview.id]), follow=True)
    interview.refresh_from_db()
    assert interview.status == Interview.Status.COMPLETED
    assert b"marked as completed" in completed.content


@pytest.mark.django_db
def test_interview_cancel_reports_the_already_cancelled_case(demo_client, demo):
    """A second cancel is a no-op, and the user is told so rather than left guessing."""
    interview = Interview.objects.filter(application__organization=demo).first()
    demo_client.post(reverse("interview-cancel", args=[ORG, interview.id]))

    response = demo_client.post(reverse("interview-cancel", args=[ORG, interview.id]), follow=True)

    assert b"already cancelled" in response.content


# ------------------------------------------------------------------------------- settings


@pytest.mark.django_db
def test_profile_updates_the_signed_in_users_name(demo_client, demo, admin_user):
    """The settings form must actually persist; it is the most-clicked write in any app."""
    response = demo_client.post(
        reverse("profile", args=[ORG]),
        {"action": "profile", "full_name": "Dana Okafor"},
        follow=True,
    )

    admin_user.refresh_from_db()
    assert admin_user.full_name == "Dana Okafor"
    assert b"Profile updated." in response.content


@pytest.mark.django_db
def test_organization_settings_issues_an_invitation(demo_client, demo):
    """Inviting a teammate must create a pending invitation and say so."""
    from apps.accounts.models import Invitation

    response = demo_client.post(
        reverse("organization-settings", args=[ORG]),
        {"email": "new.teammate@example.com", "role": "RECRUITER"},
        follow=True,
    )

    assert b"Invitation sent." in response.content
    assert Invitation.objects.filter(
        organization=demo, email="new.teammate@example.com", accepted_at__isnull=True
    ).exists()
