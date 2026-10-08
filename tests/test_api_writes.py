"""The API's write endpoints that `test_api.py` does not reach.

`test_api.py` locks the *contract* -- envelope shape, pagination, role scoping. It posts to
jobs, candidates, tasks and rejections, but it never schedules an interview or files a
scorecard, and it never exercises the task list filters. Those are real product features with
real validation, and they were sitting at zero execution.

Two of the branches here are worth a test of their own because they are easy to get wrong in a
way that fails open:

- A participant id that is not an active member of *this* workspace must be rejected, or a
  caller could drag an outsider onto an interview panel.
- `status=open` on the task list must mean both OPEN and IN_PROGRESS, not just OPEN. A filter
  that silently drops half the work is worse than no filter.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, User
from apps.applications.models import Application
from apps.interviews.models import Interview, ScorecardSubmission
from apps.jobs.models import ScorecardCriterion, ScorecardTemplate
from apps.tasks.models import Task

ORG = "northstar-labs"


@pytest.fixture
def demo_user():
    """The user the `demo` fixture's client is signed in as."""
    return User.objects.get(email="demo@example.com")


@pytest.fixture
def schedulable(demo):
    """An application plus a scorecard template on its job, ready to schedule against."""
    application = Application.objects.filter(
        organization=demo, status=Application.Status.ACTIVE
    ).first()
    assert application is not None
    # `ScorecardTemplate` is unique per (job, version) and the seeder owns version 1.
    version = 1 + ScorecardTemplate.objects.filter(job=application.job).count()
    template = ScorecardTemplate.objects.create(
        job=application.job, name=f"API rubric {version}", version=version, active=True
    )
    ScorecardCriterion.objects.create(template=template, competency="Communication", position=1)
    return application, template


# ------------------------------------------------------------------- schedule interview


@pytest.mark.django_db
def test_api_schedules_an_interview(client, demo, schedulable):
    application, template = schedulable
    start = timezone.now() + timedelta(days=2)

    response = client.post(
        reverse("api-interviews", args=[demo.slug]),
        data={
            "application": str(application.id),
            "scorecard_template": str(template.id),
            "title": "API screening call",
            "interview_type": "VIDEO",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Jakarta",
            "participants": [str(demo.memberships.first().user_id)],
        },
        content_type="application/json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["message"] == "Interview scheduled."
    interview = Interview.objects.get(id=body["data"]["id"])
    assert interview.application_id == application.id
    assert interview.template_snapshot["criteria"]


@pytest.mark.django_db
def test_api_refuses_a_participant_from_outside_the_workspace(client, demo, schedulable):
    """Otherwise a crafted payload could put an outsider on the interview panel."""
    application, template = schedulable
    outsider = User.objects.create_user(
        "outsider@example.net", "OutsidePass123!", full_name="Outsider"
    )
    start = timezone.now() + timedelta(days=2)

    response = client.post(
        reverse("api-interviews", args=[demo.slug]),
        data={
            "application": str(application.id),
            "scorecard_template": str(template.id),
            "title": "Bad panel",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "participants": [str(outsider.id)],
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "active member" in body["error"]["message"]
    assert not Interview.objects.filter(title="Bad panel").exists()


@pytest.mark.django_db
def test_api_refuses_an_interview_that_ends_before_it_starts(client, demo, schedulable):
    application, template = schedulable
    start = timezone.now() + timedelta(days=2)

    response = client.post(
        reverse("api-interviews", args=[demo.slug]),
        data={
            "application": str(application.id),
            "scorecard_template": str(template.id),
            "title": "Backwards",
            "start_at": (start + timedelta(hours=1)).isoformat(),
            "end_at": start.isoformat(),
            "participants": [str(demo.memberships.first().user_id)],
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["success"] is False


# ---------------------------------------------------------------------- submit scorecard


@pytest.mark.django_db
def test_api_submits_a_scorecard_for_a_panellist(client, demo, demo_user):
    """The interviewer's own entry point; the web scorecard page posts here."""
    interview = Interview.objects.filter(
        application__organization=demo, status=Interview.Status.SCHEDULED
    ).first()
    assert interview is not None
    # The request is made as the demo user, so they must be on the panel.
    interview.participants.get_or_create(user=demo_user)
    criteria = interview.template_snapshot["criteria"]
    assert criteria

    response = client.post(
        reverse("api-submit-scorecard", args=[demo.slug, interview.id]),
        data={
            "recommendation": "YES",
            "summary": "Strong communication, clear examples.",
            "answers": {
                row["id"]: {"rating": 3, "evidence": "Gave a concrete example."} for row in criteria
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["message"] == "Scorecard submitted."
    submission = ScorecardSubmission.objects.get(id=body["data"]["id"])
    assert submission.status == ScorecardSubmission.Status.SUBMITTED
    assert submission.locked_at is not None


@pytest.mark.django_db
def test_api_refuses_a_scorecard_from_someone_not_on_the_panel(client, demo, demo_user):
    """Panel membership is the authorisation rule, and it must be enforced on submit."""
    interview = Interview.objects.filter(application__organization=demo).first()
    interview.participants.filter(user=demo_user).delete()
    criterion_id = interview.template_snapshot["criteria"][0]["id"]

    response = client.post(
        reverse("api-submit-scorecard", args=[demo.slug, interview.id]),
        data={
            "recommendation": "YES",
            "summary": "I was not on the panel.",
            "answers": {criterion_id: {"rating": 3, "evidence": "Observed anyway."}},
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "not assigned to this interview" in response.json()["error"]["message"]


# ----------------------------------------------------------------------- task filters


@pytest.mark.django_db
def test_api_task_list_supports_status_and_assignee_filters(client, demo, demo_user):
    """`status=open` must cover OPEN *and* IN_PROGRESS -- half the visible work, otherwise."""
    me = demo_user
    other = User.objects.create_user("other.assignee@example.net", "OtherPass123!", full_name="Other")
    OrganizationMembership.objects.create(
        organization=demo, user=other, role=OrganizationMembership.Role.RECRUITER
    )
    Task.objects.create(organization=demo, title="Open task", created_by=me, assignee=me,
                        status=Task.Status.OPEN)
    Task.objects.create(organization=demo, title="Progress task", created_by=me, assignee=me,
                        status=Task.Status.IN_PROGRESS)
    Task.objects.create(organization=demo, title="Done task", created_by=me, assignee=me,
                        status=Task.Status.DONE)
    Task.objects.create(organization=demo, title="Their task", created_by=other, assignee=other,
                        status=Task.Status.OPEN)

    url = reverse("api-tasks", args=[demo.slug])
    # The seeded workspace already has tasks, so assert membership rather than equality.
    open_titles = {row["title"] for row in client.get(url, {"status": "open"}).json()["data"]["results"]}
    assert {"Open task", "Progress task"} <= open_titles
    assert "Done task" not in open_titles

    mine = {row["title"] for row in client.get(url, {"assignee": "me"}).json()["data"]["results"]}
    assert {"Open task", "Progress task", "Done task"} <= mine
    assert "Their task" not in mine

    theirs = {row["title"] for row in client.get(url, {"assignee": str(other.id)}).json()["data"]["results"]}
    assert theirs == {"Their task"}


@pytest.mark.django_db
def test_api_task_filters_treat_an_unknown_status_as_no_match(client, demo):
    """Documented as-is: an unrecognised status filters to nothing rather than erroring."""
    url = reverse("api-tasks", args=[demo.slug])
    assert client.get(url).json()["data"]["count"] > 0

    response = client.get(url, {"status": "not-a-status"})
    assert response.status_code == 200
    assert response.json()["data"]["count"] == 0


@pytest.mark.django_db
def test_api_interview_list_filters_by_status_and_application(client, demo):
    interview = Interview.objects.filter(application__organization=demo).first()
    assert interview is not None
    url = reverse("api-interviews", args=[demo.slug])

    filtered = client.get(
        url, {"application": str(interview.application_id)}
    ).json()["data"]["results"]
    assert filtered
    assert {row["application_id"] for row in filtered} == {str(interview.application_id)}

    scheduled = client.get(url, {"status": Interview.Status.SCHEDULED}).json()["data"]["results"]
    assert all(row["status"] == Interview.Status.SCHEDULED for row in scheduled)


@pytest.mark.django_db
def test_api_list_filters_never_leak_another_workspace(client, demo):
    """A foreign id must simply not match -- it must not reveal that anything exists."""
    response = client.get(
        reverse("api-interviews", args=[demo.slug]),
        {"application": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["data"]["count"] == 0
