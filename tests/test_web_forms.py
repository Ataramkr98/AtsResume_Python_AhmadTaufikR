"""The unposted form POST handlers in web/views.py (the other half of Round 7's gap).

The form GET pages render -- that was the route walk. But the handlers that *accept* a POST
were the largest single block of uncovered lines in `web/views.py` (64 missed statements):
`job_create`, `job_edit`, `candidate_create`, `candidate_edit`, `resume_download`,
`duplicate_merge`. These are exactly the "every button must work, no fake buttons" paths from
Phase 5, and several carry real business rules (bleach sanitation, optimistic/tenant scoping,
merge conflict detection) that only a POST exercises.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import OrganizationMembership, User
from apps.audit.models import AuditLog
from apps.candidates.models import Candidate, DuplicateSuggestion, Resume
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
    """A logged-in client whose role lacks `jobs.manage` and `candidates.manage`."""
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
    client = Client()
    assert client.login(username="viewer@example.com", password="LongPassword123!")
    return client


def _job_infra(organization, user, *, with_stages=("Applied", "Screening")):
    """Department/location/template the JobForm's querysets will accept as valid choices."""
    department = Department.objects.create(organization=organization, name=f"WS-{uuid.uuid4().hex[:6]}")
    location = Location.objects.create(
        organization=organization,
        name="Remote",
        city="Remote",
        country_code="ID",
        workplace_type=Location.WorkplaceType.REMOTE,
    )
    template = PipelineTemplate.objects.create(
        organization=organization, name=f"WS-{uuid.uuid4().hex[:6]}"
    )
    for position, name in enumerate(with_stages, start=1):
        PipelineTemplateStage.objects.create(
            template=template,
            name=name,
            stage_type=StageType.APPLIED if position == 1 else StageType.SCREENING,
            position=position,
        )
    return department, location, template


def _job_payload(department, location, template, user, **overrides):
    payload = {
        "title": "Senior Backend Engineer",
        "department": str(department.id),
        "location": str(location.id),
        "hiring_manager": str(user.id),
        "recruiter": str(user.id),
        "pipeline_template": str(template.id),
        "employment_type": Job.EmploymentType.FULL_TIME,
        "headcount": "1",
        "description_html": "<p>A real posting.</p>",
        "requirements_html": "<p>Python.</p>",
        "min_experience_months": "",
        "education_level": "",
    }
    payload.update(overrides)
    return payload


# ----------------------------------------------------------------------------- job_create


@pytest.mark.django_db
def test_job_create_get_renders_the_form(demo_client, demo, admin_user):
    """The New Job page must render -- a blank form is the entry point to the action."""
    response = demo_client.get(reverse("job-create", args=[ORG]))
    assert response.status_code == 200
    assert b"Create a job" in response.content


@pytest.mark.django_db
def test_job_create_post_persists_a_draft_with_pipeline_stages(demo_client, demo, admin_user):
    """Happy path: a valid draft is created as DRAFT, seeds its pipeline, audits, redirects."""
    department, location, template = _job_infra(demo, admin_user)
    before = Job.objects.count()

    response = demo_client.post(
        reverse("job-create", args=[ORG]), _job_payload(department, location, template, admin_user)
    )

    assert response.status_code == 302
    assert Job.objects.count() == before + 1
    job = Job.objects.latest("created_at")
    assert job.status == Job.Status.DRAFT
    # The pipeline template's stages were materialised onto the job.
    assert job.stages.count() == 2
    assert AuditLog.objects.filter(organization=demo, action="job.created", resource_id=str(job.id)).exists()


@pytest.mark.django_db
def test_job_create_post_redisplays_on_invalid_form(demo_client, demo, admin_user):
    """Missing required fields must re-render with errors, never silently create a job."""
    department, location, template = _job_infra(demo, admin_user)
    before = Job.objects.count()

    response = demo_client.post(
        reverse("job-create", args=[ORG]),
        # No title -> required field error.
        _job_payload(department, location, template, admin_user, title=""),
    )

    assert response.status_code == 200
    assert Job.objects.count() == before
    assert b"required" in response.content.lower()


@pytest.mark.django_db
def test_job_create_refuses_a_user_without_jobs_manage(demo_client, demo, admin_user, interviewer):
    """Permission gating is a 404 (Http404), consistent with every other org-scoped view."""
    department, location, template = _job_infra(demo, admin_user)
    before_jobs = Job.objects.count()

    response = interviewer.post(
        reverse("job-create", args=[ORG]), _job_payload(department, location, template, admin_user)
    )

    assert response.status_code == 404
    # The denied request must not have created anything.
    assert Job.objects.count() == before_jobs


# ------------------------------------------------------------------------------- job_edit


@pytest.mark.django_db
def test_job_edit_get_renders_with_the_job(demo_client, demo, admin_user):
    from apps.jobs.services import create_job

    department, location, template = _job_infra(demo, admin_user)
    job = create_job(
        organization=demo,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Initial",
            "department": department,
            "location": location,
            "hiring_manager": admin_user,
            "recruiter": admin_user,
            "pipeline_template": template,
            "employment_type": Job.EmploymentType.FULL_TIME,
            "headcount": 1,
            "description_html": "<p>old</p>",
        },
    )

    response = demo_client.get(reverse("job-edit", args=[ORG, job.id]))
    assert response.status_code == 200
    assert b"Edit job" in response.content


@pytest.mark.django_db
def test_job_edit_post_sanitizes_html_and_bumps_version(demo_client, demo, admin_user):
    """The description is run through bleach; a stored XSS attempt must be stripped, not stored."""
    from apps.jobs.services import create_job

    department, location, template = _job_infra(demo, admin_user)
    job = create_job(
        organization=demo,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Initial",
            "department": department,
            "location": location,
            "hiring_manager": admin_user,
            "recruiter": admin_user,
            "pipeline_template": template,
            "employment_type": Job.EmploymentType.FULL_TIME,
            "headcount": 1,
            "description_html": "<p>old</p>",
        },
    )
    version_before = job.version

    response = demo_client.post(
        reverse("job-edit", args=[ORG, job.id]),
        _job_payload(
            department,
            location,
            template,
            admin_user,
            title="Edited title",
            description_html="<p>Hi</p><script>alert(1)</script><strong>there</strong>",
        ),
    )

    assert response.status_code == 302
    job.refresh_from_db()
    assert job.title == "Edited title"
    assert job.version == version_before + 1
    assert "<script" not in job.description_html
    assert "alert(1)" in job.description_html  # script body is kept as text, tag is dropped
    assert AuditLog.objects.filter(organization=demo, action="job.updated", resource_id=str(job.id)).exists()


@pytest.mark.django_db
def test_job_edit_refuses_an_unknown_job(demo_client, demo, admin_user):
    """A fabricated job id must raise 404 before any business rule -- the view uses get_object_or_404."""
    response = demo_client.get(reverse("job-edit", args=[ORG, uuid.uuid4()]))
    assert response.status_code == 404


# -------------------------------------------------------------------------- candidate_create


@pytest.mark.django_db
def test_candidate_create_post_persists_encrypted_contact(demo_client, demo, admin_user):
    """A manual candidate is written with the email encrypted + hashed for lookups."""
    from apps.common.crypto import lookup_hash

    payload = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "current_title": "Engineer",
        "current_company": "Analytical",
        "city": "London",
        "country_code": "GB",
        "total_experience_months": "120",
        "education_level": "doctorate",
        "source_first": "MANUAL",
        "summary": "Pioneer.",
        "email": "ada.lovelace@example.com",
        "phone": "+44 20 7946 0000",
    }
    before = Candidate.objects.count()

    response = demo_client.post(reverse("candidate-create", args=[ORG]), payload)

    assert response.status_code == 302
    assert Candidate.objects.count() == before + 1
    candidate = Candidate.objects.latest("created_at")
    assert candidate.primary_email == "ada.lovelace@example.com"
    assert candidate.email_hash == lookup_hash("ada.lovelace@example.com")
    assert candidate.source_first == "MANUAL"
    assert AuditLog.objects.filter(organization=demo, action="candidate.created", resource_id=str(candidate.id)).exists()


@pytest.mark.django_db
def test_candidate_create_post_redisplays_on_invalid_email(demo_client, demo, admin_user):
    before = Candidate.objects.count()

    response = demo_client.post(
        reverse("candidate-create", args=[ORG]),
        {
            "first_name": "Ada",
            "source_first": "MANUAL",
            "email": "not-an-email",
        },
    )

    assert response.status_code == 200
    assert Candidate.objects.count() == before


@pytest.mark.django_db
def test_candidate_create_refuses_a_user_without_candidates_manage(demo_client, demo, admin_user, interviewer):
    before = Candidate.objects.count()
    response = interviewer.post(
        reverse("candidate-create", args=[ORG]),
        {"first_name": "Ada", "source_first": "MANUAL", "email": "ada@example.com"},
    )
    assert response.status_code == 404
    assert Candidate.objects.count() == before


# --------------------------------------------------------------------------- candidate_edit


@pytest.mark.django_db
def test_candidate_edit_post_updates_and_normalizes_email(demo_client, demo, admin_user):
    candidate = Candidate.objects.create(
        organization=demo,
        first_name="Ada",
        last_name="Lovelace",
        city="London",
        country_code="GB",
        total_experience_months=120,
        education_level="doctorate",
    )
    candidate.set_email("ada@example.com")
    candidate.save()

    response = demo_client.post(
        reverse("candidate-edit", args=[ORG, candidate.id]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "current_title": "Principal Engineer",
            "current_company": "Analytical",
            "city": "London",
            "country_code": "GB",
            "total_experience_months": "120",
            "education_level": "doctorate",
            "source_first": "MANUAL",
            "summary": "Updated.",
            "email": "Ada.Lovelace@Example.COM",  # mixed case -> must be casefolded
            "phone": "",
        },
    )

    assert response.status_code == 302
    candidate.refresh_from_db()
    assert candidate.current_title == "Principal Engineer"
    assert candidate.primary_email == "ada.lovelace@example.com"
    assert AuditLog.objects.filter(organization=demo, action="candidate.updated", resource_id=str(candidate.id)).exists()


@pytest.mark.django_db
def test_candidate_edit_post_redisplays_on_invalid_email(demo_client, demo, admin_user):
    candidate = Candidate.objects.create(
        organization=demo,
        first_name="Ada",
        last_name="Lovelace",
        city="London",
        country_code="GB",
        total_experience_months=120,
        education_level="doctorate",
    )
    candidate.set_email("ada@example.com")
    candidate.save()
    title_before = candidate.current_title

    response = demo_client.post(
        reverse("candidate-edit", args=[ORG, candidate.id]),
        {"first_name": "Ada", "source_first": "MANUAL", "email": "nope"},
    )

    assert response.status_code == 200
    candidate.refresh_from_db()
    assert candidate.current_title == title_before


# --------------------------------------------------------------------------- resume_download


@pytest.mark.django_db
def test_resume_download_streams_the_file_and_audits(demo_client, demo, admin_user):
    candidate = Candidate.objects.create(
        organization=demo,
        first_name="Ada",
        last_name="Lovelace",
        city="London",
        country_code="GB",
        total_experience_months=120,
        education_level="doctorate",
    )
    candidate.set_email("ada@example.com")
    candidate.save()
    resume = Resume.objects.create(
        organization=demo,
        candidate=candidate,
        file=SimpleUploadedFile("ada-cv.pdf", b"%PDF-1.4 resume body", content_type="application/pdf"),
        original_name="ada-cv.pdf",
        mime_type="application/pdf",
        size_bytes=18,
        checksum_sha256="0" * 64,
    )

    response = demo_client.get(reverse("resume-download", args=[ORG, resume.id]))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert "ada-cv.pdf" in response["Content-Disposition"]
    body = b"".join(response.streaming_content)
    assert body == b"%PDF-1.4 resume body"
    assert AuditLog.objects.filter(organization=demo, action="resume.downloaded", resource_id=str(resume.id)).exists()
    response.close()  # release the open file handle before GC (-W error treats leaks as failures)


@pytest.mark.django_db
def test_resume_download_404_for_an_unknown_resume(demo_client, demo, admin_user):
    response = demo_client.get(reverse("resume-download", args=[ORG, uuid.uuid4()]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------- duplicate_merge


def _two_candidates(organization):
    a = Candidate.objects.create(
        organization=organization, first_name="Ada", last_name="Lovelace",
        city="London", country_code="GB", total_experience_months=120, education_level="doctorate",
    )
    a.set_email("ada@example.com")
    a.save()
    b = Candidate.objects.create(
        organization=organization, first_name="Ada", last_name="Lovelace",
        city="London", country_code="GB", total_experience_months=120, education_level="doctorate",
    )
    b.set_email("ada.dup@example.com")
    b.save()
    return a, b


@pytest.mark.django_db
def test_duplicate_merge_merges_and_soft_deletes_the_duplicate(demo_client, demo, admin_user):
    a, b = _two_candidates(demo)
    suggestion = DuplicateSuggestion.objects.create(
        organization=demo,
        candidate=b,
        possible_duplicate=a,
        score="0.97",
        status=DuplicateSuggestion.Status.PENDING,
    )

    response = demo_client.post(reverse("duplicate-merge", args=[ORG, suggestion.id]))

    assert response.status_code == 302
    assert response.url == reverse("candidate-detail", args=[ORG, a.id])
    b.refresh_from_db()
    assert b.deleted_at is not None
    suggestion.refresh_from_db()
    assert suggestion.status == DuplicateSuggestion.Status.MERGED
    assert AuditLog.objects.filter(organization=demo, action="candidate.merged", resource_id=str(a.id)).exists()


@pytest.mark.django_db
def test_duplicate_merge_refuses_conflicting_applications(demo_client, demo, admin_user):
    """If both candidates already applied to the same job the merge must be blocked, not forced.

    The view turns the ValidationError into an error message and sends the user back to the
    candidate list -- the duplicate must stay exactly as it was.
    """
    from apps.applications.models import Application
    from apps.jobs.services import create_job

    a, b = _two_candidates(demo)
    department, location, template = _job_infra(demo, admin_user)
    job = create_job(
        organization=demo,
        actor=admin_user,
        membership=admin_user.memberships.get(organization=demo),
        data={
            "title": "Shared req",
            "department": department,
            "location": location,
            "hiring_manager": admin_user,
            "recruiter": admin_user,
            "pipeline_template": template,
            "employment_type": Job.EmploymentType.FULL_TIME,
            "headcount": 1,
            "description_html": "<p>x</p>",
        },
    )
    stage = job.stages.first()
    Application.objects.create(organization=demo, job=job, candidate=a, current_stage=stage, source="MANUAL")
    Application.objects.create(organization=demo, job=job, candidate=b, current_stage=stage, source="MANUAL")

    suggestion = DuplicateSuggestion.objects.create(
        organization=demo,
        candidate=b,
        possible_duplicate=a,
        score="0.97",
        status=DuplicateSuggestion.Status.PENDING,
    )

    response = demo_client.post(reverse("duplicate-merge", args=[ORG, suggestion.id]), follow=True)

    assert response.status_code == 200
    assert b"conflicting" in response.content.lower()
    b.refresh_from_db()
    assert b.deleted_at is None
    suggestion.refresh_from_db()
    assert suggestion.status == DuplicateSuggestion.Status.PENDING


@pytest.mark.django_db
def test_duplicate_merge_handles_an_unknown_suggestion(demo_client, demo, admin_user):
    """A stale or forged suggestion id must not crash -- it is reported and nothing is merged."""
    a, b = _two_candidates(demo)

    response = demo_client.post(reverse("duplicate-merge", args=[ORG, uuid.uuid4()]), follow=True)

    assert response.status_code == 200
    assert b"not found" in response.content.lower()
    b.refresh_from_db()
    assert b.deleted_at is None
