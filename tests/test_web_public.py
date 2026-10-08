"""Public surface of web/views.py.

The route walk proves every page *renders*; it does not prove the candidate funnel *works*.
`public_apply` is the product's only anonymous write path and its POST branch had never been
run by a test -- a break there silently rejects every applicant, which is the worst kind of
regression because there is no internal signal that applications stopped arriving.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import Organization, User
from apps.applications.models import Application
from apps.jobs.models import (
    Department,
    Job,
    Location,
    PipelineStage,
    PipelineTemplate,
    StageType,
)

ORG = "northstar-labs"


@pytest.fixture
def workspace(seeded_workspace):
    """The seeded workspace, without logging anyone in -- a true anonymous visitor."""
    return Organization.objects.get(slug=ORG)


def _public_job(organization, user):
    """An open job with one active stage, in the demo workspace."""
    department = Department.objects.create(organization=organization, name="Pub")
    location = Location.objects.create(
        organization=organization, name="Remote", city="Remote", country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    pipeline = PipelineTemplate.objects.create(organization=organization, name="Pub")
    job = Job.objects.create(
        organization=organization, department=department, location=location,
        hiring_manager=user, recruiter=user, pipeline_template=pipeline,
        title="Backend Engineer", slug="backend-pub", status=Job.Status.OPEN,
        employment_type=Job.EmploymentType.FULL_TIME, description_html="<p>x</p>",
        min_experience_months=0, education_level="bachelor",
    )
    PipelineStage.objects.create(
        job=job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    return job


@pytest.mark.django_db
def test_public_apply_creates_an_application(client, workspace):
    """The candidate funnel end to end: resume stored, application entered at stage one."""
    user = User.objects.get(email="demo@example.com")
    job = _public_job(workspace, user)
    resume = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 fake resume", content_type="application/pdf")

    response = client.post(
        reverse("public-apply", args=[ORG, job.slug]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "",
            "consent": "on",
            "resume": resume,
        },
    )

    assert response.status_code == 202
    application = Application.objects.get(job=job)
    assert application.candidate.first_name == "Ada"
    assert application.resume is not None


@pytest.mark.django_db
def test_public_apply_rejects_a_missing_consent(client, workspace):
    """No consent, no application -- the service raises and the view must surface it, not crash."""
    user = User.objects.get(email="demo@example.com")
    job = _public_job(workspace, user)
    resume = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 fake resume", content_type="application/pdf")

    response = client.post(
        reverse("public-apply", args=[ORG, job.slug]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "",
            "resume": resume,
        },
    )

    assert response.status_code == 200
    assert Application.objects.filter(job=job).count() == 0


@pytest.mark.django_db
def test_public_apply_rejects_a_non_pdf(client, workspace):
    user = User.objects.get(email="demo@example.com")
    job = _public_job(workspace, user)
    bad = SimpleUploadedFile("notes.txt", b"not a pdf", content_type="text/plain")

    response = client.post(
        reverse("public-apply", args=[ORG, job.slug]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "consent": "on",
            "resume": bad,
        },
    )

    assert response.status_code == 200
    assert Application.objects.filter(job=job).count() == 0


@pytest.mark.django_db
def test_public_apply_is_idempotent_per_candidate(client, workspace):
    """A second submission from the same email must not create a second application."""
    user = User.objects.get(email="demo@example.com")
    job = _public_job(workspace, user)
    base = {
        "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        "phone": "", "consent": "on",
    }

    first = client.post(
        reverse("public-apply", args=[ORG, job.slug]),
        {**base, "resume": SimpleUploadedFile("resume.pdf", b"%PDF-1.4 fake resume", content_type="application/pdf")},
    )
    # A fresh upload object per request -- file uploads are read-once streams.
    second = client.post(
        reverse("public-apply", args=[ORG, job.slug]),
        {**base, "resume": SimpleUploadedFile("resume.pdf", b"%PDF-1.4 fake resume", content_type="application/pdf")},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert Application.objects.filter(job=job).count() == 1


@pytest.mark.django_db
def test_landing_redirects_signed_in_users(demo_client):
    response = demo_client.get(reverse("home"))
    assert response.status_code == 302
    assert response.url == reverse("app-entry")


@pytest.mark.django_db
def test_landing_exposes_demo_credentials_to_anonymous_visitors(client, workspace):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b"demo@example.com" in response.content
