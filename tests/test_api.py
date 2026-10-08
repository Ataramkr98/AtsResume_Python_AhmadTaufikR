"""Contract tests for the REST API.

These lock in the response envelope, pagination and the role scoping that the web UI relies on,
so a future refactor cannot quietly change the shape clients depend on.
"""

from __future__ import annotations

from django.urls import reverse

from apps.accounts.models import Organization
from apps.applications.models import Application
from apps.jobs.models import Job
from apps.tasks.models import Task


def test_success_payloads_use_the_standard_envelope(client, demo):
    response = client.get(reverse("api-jobs", args=[demo.slug]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"success", "message", "data"}
    assert body["success"] is True
    assert body["message"] == ""
    assert set(body["data"]) == {"count", "next", "previous", "results"}


def test_error_payloads_use_the_standard_envelope(client, demo):
    response = client.get(reverse("api-job", args=[demo.slug, "00000000-0000-0000-0000-000000000000"]))
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert set(body["error"]) == {"code", "message", "fields", "request_id"}


def test_list_endpoints_paginate_and_respect_page_size(client, demo):
    first = client.get(reverse("api-candidates", args=[demo.slug])).json()["data"]
    assert first["count"] == 34
    assert len(first["results"]) == 25
    assert first["next"] is not None

    second = client.get(reverse("api-candidates", args=[demo.slug]), {"page": 2}).json()["data"]
    assert len(second["results"]) == 9
    assert second["next"] is None

    sized = client.get(reverse("api-candidates", args=[demo.slug]), {"page_size": 5}).json()["data"]
    assert len(sized["results"]) == 5


def test_dashboard_and_reports_agree_on_the_funnel(client, demo):
    dashboard = client.get(reverse("api-dashboard", args=[demo.slug])).json()["data"]
    report = client.get(reverse("api-reports", args=[demo.slug]), {"range": "90"}).json()["data"]

    assert dashboard["funnel"] == report["funnel"]
    assert dashboard["time_to_hire"] == report["time_to_hire"]
    assert dashboard["summary"]["open_jobs"] == 4
    # The funnel must be monotonically non-increasing — a candidate cannot skip the applied cohort.
    counts = [row["count"] for row in dashboard["funnel"]]
    assert counts == sorted(counts, reverse=True)


def test_reports_are_scoped_and_readable_for_an_auditor(client, demo):
    from apps.accounts.models import OrganizationMembership, User

    auditor = User.objects.create_user(
        "auditor@northstar.example", "AuditorPass123!", full_name="Nina Auditor"
    )
    OrganizationMembership.objects.create(
        organization=demo, user=auditor, role=OrganizationMembership.Role.AUDITOR
    )
    client.force_login(auditor)

    assert client.get(reverse("api-reports", args=[demo.slug])).status_code == 200
    # An auditor has read-only reporting access, so write endpoints must reject them.
    job = Job.objects.filter(organization=demo).first()
    assert client.patch(
        reverse("api-job", args=[demo.slug, job.id]),
        data={"headcount": 5},
        content_type="application/json",
    ).status_code == 404


def test_job_create_update_and_transition(client, demo):
    from apps.jobs.models import Department, Location, PipelineTemplate

    department = Department.objects.filter(organization=demo).first()
    location = Location.objects.filter(organization=demo).first()
    template = PipelineTemplate.objects.filter(organization=demo).first()

    created = client.post(
        reverse("api-jobs", args=[demo.slug]),
        data={
            "title": "Staff Data Engineer",
            "department": str(department.id),
            "location": str(location.id),
            "pipeline_template": str(template.id),
            "employment_type": "FULL_TIME",
            "headcount": 1,
            "description_html": "<p>Own the data platform.</p>",
            "min_experience_months": 60,
            "education_level": "bachelor",
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["message"] == "Job draft created."
    job_id = body["data"]["id"]
    assert body["data"]["status"] == Job.Status.DRAFT

    patched = client.patch(
        reverse("api-job", args=[demo.slug, job_id]),
        data={"headcount": 3},
        content_type="application/json",
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["headcount"] == 3

    transitioned = client.post(
        reverse("api-job-transition", args=[demo.slug, job_id]),
        data={"target_status": Job.Status.OPEN},
        content_type="application/json",
    )
    assert transitioned.status_code == 200
    assert transitioned.json()["data"]["status"] == Job.Status.OPEN


def test_job_transition_rejects_an_illegal_move(client, demo):
    job = Job.objects.get(organization=demo, slug="solutions-engineer")  # CLOSED
    response = client.post(
        reverse("api-job-transition", args=[demo.slug, job.id]),
        data={"target_status": Job.Status.OPEN},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_candidate_create_and_update_round_trip(client, demo):
    from apps.candidates.models import Candidate

    created = client.post(
        reverse("api-candidates", args=[demo.slug]),
        data={
            "first_name": "Tari",
            "last_name": "Rahman",
            "email": "tari.rahman@example.net",
            "phone": "+62 811 000 999",
            "city": "Jakarta",
            "country_code": "id",
            "current_title": "Backend Engineer",
            "total_experience_months": 60,
            "education_level": "bachelor",
            "source_first": "REFERRAL",
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    candidate_id = created.json()["data"]["id"]

    candidate = Candidate.objects.get(id=candidate_id)
    # The contact details must be stored encrypted and remain readable through the property.
    assert candidate.primary_email == "tari.rahman@example.net"
    assert candidate.primary_phone == "+62 811 000 999"
    assert candidate.country_code == "ID"

    patched = client.patch(
        reverse("api-candidate", args=[demo.slug, candidate_id]),
        data={"current_company": "Northstar Labs"},
        content_type="application/json",
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["current_company"] == "Northstar Labs"
    candidate.refresh_from_db()
    assert candidate.primary_email == "tari.rahman@example.net"


def test_task_lifecycle_through_the_api(client, demo):
    assignee = Task.objects.filter(organization=demo).first().assignee

    created = client.post(
        reverse("api-tasks", args=[demo.slug]),
        data={
            "title": "Book the debrief room",
            "description": "Confirm the panel can all attend.",
            "priority": "HIGH",
            "assignee": str(assignee.id),
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    task_id = created.json()["data"]["id"]

    done = client.patch(
        reverse("api-task", args=[demo.slug, task_id]),
        data={"status": Task.Status.DONE},
        content_type="application/json",
    )
    assert done.status_code == 200
    assert done.json()["data"]["status"] == Task.Status.DONE

    task = Task.objects.get(id=task_id)
    assert task.completed_at is not None


def test_task_status_change_is_audited(client, demo):
    from apps.audit.models import AuditLog

    task = Task.objects.filter(organization=demo).exclude(status=Task.Status.DONE).first()
    client.patch(
        reverse("api-task", args=[demo.slug, task.id]),
        data={"status": Task.Status.DONE},
        content_type="application/json",
    )
    assert AuditLog.objects.filter(
        organization=demo, action="task.updated", resource_id=str(task.id)
    ).exists()


def test_interview_cancel_is_idempotent_and_audited(client, demo):
    from apps.audit.models import AuditLog
    from apps.interviews.models import Interview

    interview = Interview.objects.filter(
        application__organization=demo, status=Interview.Status.SCHEDULED
    ).first()
    url = reverse("api-interview-cancel", args=[demo.slug, interview.id])

    first = client.post(url)
    assert first.status_code == 200
    assert first.json()["data"]["status"] == Interview.Status.CANCELLED

    second = client.post(url)
    assert second.status_code == 200
    assert second.json()["message"] == "No change — the interview was already in that state."
    assert (
        AuditLog.objects.filter(
            organization=demo, action="interview.cancelled", resource_id=str(interview.id)
        ).count()
        == 1
    )


def test_reject_application_requires_a_tenant_reason(client, demo):
    from apps.applications.models import RejectionReason

    application = Application.objects.filter(organization=demo, status=Application.Status.ACTIVE).first()
    reason = RejectionReason.objects.filter(organization=demo).first()
    url = reverse("api-reject-application", args=[demo.slug, application.id])

    rejected = client.post(url, data={"reason": str(reason.id)}, content_type="application/json")
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == Application.Status.REJECTED

    other = Organization.objects.create(name="Elsewhere", slug="elsewhere")
    foreign = RejectionReason.objects.create(organization=other, name="Foreign reason")
    second = Application.objects.filter(
        organization=demo, status=Application.Status.ACTIVE
    ).exclude(id=application.id).first()
    blocked = client.post(
        reverse("api-reject-application", args=[demo.slug, second.id]),
        data={"reason": str(foreign.id)},
        content_type="application/json",
    )
    assert blocked.status_code == 400
    assert blocked.json()["success"] is False


def test_anonymous_requests_are_rejected(demo):
    from django.test import Client

    anonymous = Client()
    for name in ["api-jobs", "api-candidates", "api-tasks", "api-reports", "api-dashboard"]:
        response = anonymous.get(reverse(name, args=[demo.slug]))
        assert response.status_code in {401, 403}, name
