"""Every route the product exposes, walked as the demo administrator.

The suite already covers behaviour (services, API contracts, permissions). What it did not
cover was the *surface*: whether every page a user can click through to actually renders. A
template typo or a renamed URL name would pass every other test and still ship a broken page.

This walks the whole route table and fails with the full list of broken routes, so the
"everything loads" guarantee is enforced by CI rather than by a one-off manual check.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.applications.models import Application
from apps.candidates.models import Resume
from apps.interviews.models import Interview
from apps.jobs.models import Job
from apps.tasks.models import Task

ORG = "northstar-labs"


def _route_table():
    """Build (label, url, expected_status) for every route worth exercising.

    Reads the seeded workspace, so it must only be called once the session fixture has run.
    """
    base = f"/app/{ORG}"
    open_job = Job.objects.filter(organization__slug=ORG, status=Job.Status.OPEN).first()
    paused_job = Job.objects.filter(organization__slug=ORG).exclude(status=Job.Status.OPEN).first()
    application = Application.objects.filter(organization__slug=ORG).first()
    candidate = application.candidate
    interview = Interview.objects.filter(application__organization__slug=ORG).first()
    resume = Resume.objects.filter(organization__slug=ORG).first()
    task = Task.objects.filter(organization__slug=ORG).first()

    return [
        # --- public surface -------------------------------------------------
        ("landing", "/", 200),
        ("health check", "/healthz", 200),
        ("readiness check", "/readyz", 200),
        ("careers board", f"/careers/{ORG}/", 200),
        ("careers job detail", f"/careers/{ORG}/jobs/{open_job.slug}/", 200),
        ("careers apply form", f"/careers/{ORG}/jobs/{open_job.slug}/apply/", 200),
        ("careers hides paused job", f"/careers/{ORG}/jobs/{paused_job.slug}/", 404),
        ("login", reverse("login"), 200),
        ("custom 404", "/does-not-exist/", 404),
        # --- workspace ------------------------------------------------------
        ("app entry redirect", "/app/", 200),
        ("dashboard", f"{base}/", 200),
        ("jobs list", f"{base}/jobs/", 200),
        ("jobs list filtered", f"{base}/jobs/?status=OPEN&q=engineer", 200),
        ("job detail", f"{base}/jobs/{open_job.id}/", 200),
        ("job create form", f"{base}/jobs/new/", 200),
        ("job edit form", f"{base}/jobs/{open_job.id}/edit/", 200),
        ("candidates list", f"{base}/candidates/", 200),
        ("candidates list filtered", f"{base}/candidates/?q=putri", 200),
        ("candidate detail", f"{base}/candidates/{candidate.id}/", 200),
        ("candidate create form", f"{base}/candidates/new/", 200),
        ("candidate edit form", f"{base}/candidates/{candidate.id}/edit/", 200),
        ("pipeline overview", f"{base}/pipeline/", 200),
        ("pipeline for one job", f"{base}/pipeline/{open_job.id}/", 200),
        ("interviews list", f"{base}/interviews/", 200),
        ("interviews list filtered", f"{base}/interviews/?status=SCHEDULED", 200),
        ("interview scorecard", f"{base}/interviews/{interview.id}/scorecard/", 200),
        ("tasks list", f"{base}/tasks/", 200),
        ("tasks list filtered", f"{base}/tasks/?status=open&assignee=me", 200),
        ("task create form", f"{base}/tasks/new/", 200),
        ("reports", f"{base}/reports/", 200),
        ("reports 30 day window", f"{base}/reports/?range=30", 200),
        ("reports all time", f"{base}/reports/?range=all", 200),
        ("reports CSV export", f"{base}/reports/export/applications.csv?range=90", 200),
        ("audit log", f"{base}/audit/", 200),
        ("audit log filtered", f"{base}/audit/?q=job&action=job.status_changed", 200),
        ("organization settings", f"{base}/settings/", 200),
        ("profile", f"{base}/profile/", 200),
        ("resume download", f"{base}/resumes/{resume.id}/download/", 200),
        # --- operations -----------------------------------------------------
        ("django admin login", "/ops/login/", 200),
        ("django admin index", "/ops/", 200),
        ("task status endpoint reachable", f"{base}/tasks/{task.id}/status/", 405),
    ]


@pytest.mark.django_db
def test_every_route_renders(demo_client):
    """Walk the full route table and report every broken route at once."""
    failures = []
    checked = 0

    for label, url, expected in _route_table():
        checked += 1
        try:
            response = demo_client.get(url, follow=True)
            # Streaming responses (the resume download) have no .content until consumed.
            if hasattr(response, "streaming_content"):
                try:
                    body = b"".join(response.streaming_content)
                finally:
                    # Django's FileResponse registers the file handle in _resource_closers and
                    # releases it from Response.close(). A WSGI server always calls close(); the
                    # test client does not, so without this the handle leaks until GC.
                    response.close()
            else:
                body = response.content
        except Exception as exc:  # noqa: BLE001 - a raising view is exactly what we are catching
            failures.append(f"{label} ({url}) raised {type(exc).__name__}: {exc}")
            continue

        if response.status_code != expected:
            failures.append(f"{label} ({url}) expected {expected}, got {response.status_code}")
        elif response.status_code == 200 and not body:
            failures.append(f"{label} ({url}) returned an empty body")

    assert not failures, (
        f"{len(failures)} of {checked} routes are broken:\n  - " + "\n  - ".join(failures)
    )


@pytest.mark.django_db
def test_workspace_routes_require_authentication(client, seeded_workspace):
    """No workspace page may render for an anonymous visitor."""
    anonymous = [url for _, url, _ in _route_table() if url.startswith(f"/app/{ORG}/")]

    for url in anonymous:
        response = client.get(url)
        assert response.status_code in {302, 404}, f"{url} leaked to anonymous users"


@pytest.mark.django_db
def test_no_route_returns_a_server_error(demo_client):
    """A 5xx anywhere is a bug, even if the route is otherwise expected to work."""
    for label, url, _ in _route_table():
        response = demo_client.get(url)
        try:
            assert response.status_code < 500, f"{label} ({url}) returned {response.status_code}"
        finally:
            response.close()
