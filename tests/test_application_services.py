"""Write-path authorization for applications (`apps/applications/services.py`).

`policies.py` decides what you can *see*; this module decides what you can *move*. Both matter,
but only one of them is on the security boundary of a mutation, and it had the same uncovered
branches as `policies.py` did: the hiring-manager ownership check and the recruiter
department/location scope.

Two findings came out of writing these, and both are recorded here because they change how the
code should be read:

1. **`_check_job_scope` is a backstop, not the first line of defence.** When an out-of-scope
   recruiter or a non-owning hiring manager tries to move an application, the error is
   "Application not found" -- not "outside your department scope". `scope_applications` has
   already filtered the row out before the guard runs. That is the better behaviour (it does not
   confirm the resource exists), and the end-to-end tests below assert the refusal without
   pinning the message, so a future change to which layer fires first will not break them. The
   guard is therefore also tested **directly**, which is the only way to reach its branches.

2. **The two layers compare scope differently.** `policies.py` asks
   `department_id__in=membership.department_scope` (UUID comparison) while `services.py` asks
   `str(job.department_id) not in membership.department_scope` (string comparison). Two
   implementations of the same rule can drift, and the drift is invisible until one of them
   disagrees with the other. These tests pin both to the same *observable* outcome rather than
   refactoring a working authorization check, which would be the far riskier change.

The remaining branches -- "only active applications can move", "this stage needs an interview",
"this stage needs a scorecard" -- are the product's actual pipeline rules. They were asserted in
the README and in the UI copy, but never by a test.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, User
from apps.applications.models import Application, RejectionReason
from apps.applications.services import _check_job_scope, move_application_stage, reject_application
from apps.candidates.models import Candidate
from apps.interviews.models import Interview, ScorecardSubmission
from apps.jobs.models import (
    Department,
    Job,
    JobTeamMember,
    Location,
    PipelineStage,
    PipelineTemplate,
    StageType,
)

ORG = "northstar-labs"


def _member(organization, email, *, role, department_scope=None, location_scope=None):
    user = User.objects.create_user(email, "LongPassword123!", full_name=email.split("@")[0])
    membership = OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
        department_scope=department_scope or [],
        location_scope=location_scope or [],
    )
    return user, membership


def _location(organization, name):
    return Location.objects.create(
        organization=organization,
        name=name,
        city=name,
        country_code="ID",
        workplace_type=Location.WorkplaceType.ONSITE,
    )


def _job(organization, owner, department, location, title):
    pipeline = PipelineTemplate.objects.create(organization=organization, name=f"P-{uuid.uuid4().hex[:6]}")
    return Job.objects.create(
        organization=organization,
        department=department,
        location=location,
        hiring_manager=owner,
        recruiter=owner,
        pipeline_template=pipeline,
        title=title,
        slug=uuid.uuid4().hex[:12],
        status=Job.Status.OPEN,
        employment_type=Job.EmploymentType.FULL_TIME,
        description_html="<p>x</p>",
        min_experience_months=0,
        education_level="bachelor",
    )


def _stage(job, name, *, position=1, stage_type=StageType.CUSTOM, **flags):
    return PipelineStage.objects.create(
        job=job, name=name, stage_type=stage_type, position=position, active=True, **flags
    )


def _application(organization, job, stage, email="candidate@example.com"):
    candidate = Candidate.objects.create(
        organization=organization,
        first_name="Casey",
        last_name="Yates",
        city="Jakarta",
        country_code="ID",
        total_experience_months=24,
        education_level="bachelor",
    )
    candidate.set_email(email)
    candidate.save()
    return Application.objects.create(
        organization=organization, job=job, candidate=candidate, current_stage=stage, source="MANUAL"
    )


@pytest.fixture
def scoped(demo, admin_user):
    """A recruiter-scoped pair of departments and locations, plus a job and application."""
    tag = uuid.uuid4().hex[:6]
    engineering = Department.objects.create(organization=demo, name=f"Eng-{tag}")
    sales = Department.objects.create(organization=demo, name=f"Sales-{tag}")
    jakarta = _location(demo, f"Jakarta-{tag}")
    bandung = _location(demo, f"Bandung-{tag}")
    job = _job(demo, admin_user, engineering, jakarta, "Scoped role")
    applied = _stage(job, "Applied", position=1, stage_type=StageType.APPLIED)
    screening = _stage(job, "Screening", position=2, stage_type=StageType.SCREENING)
    return job, applied, screening, engineering, sales, jakarta, bandung


# ---------------------------------------------------------- object-level authorization


@pytest.mark.django_db
def test_hiring_manager_cannot_move_an_application_on_a_peers_requisition(demo, admin_user, scoped):
    """Seeing is not moving: the ownership rule must hold on the write path too.

    Note *which* error comes back. `scope_applications` has already filtered the application
    out, so the refusal is "Application not found" rather than the more revealing "you do not
    have access to this job". That is the better answer -- it does not confirm the resource
    exists -- and it means `_check_job_scope` is a backstop, not the first line of defence.
    """
    job, applied, screening, *_ = scoped
    peer, membership = _member(
        demo, "peer.hm@example.com", role=OrganizationMembership.Role.HIRING_MANAGER
    )
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError):
        move_application_stage(
            application_id=application.id,
            target_stage_id=screening.id,
            organization=demo,
            actor=peer,
            membership=membership,
            idempotency_key="peer-1",
            expected_version=application.version,
        )

    application.refresh_from_db()
    assert application.current_stage_id == applied.id


@pytest.mark.django_db
def test_hiring_manager_may_move_an_application_on_their_own_requisition(demo, scoped):
    job, applied, screening, *_ = scoped
    owner, membership = _member(
        demo, "owner.hm@example.com", role=OrganizationMembership.Role.HIRING_MANAGER
    )
    job.hiring_manager = owner
    job.save(update_fields=["hiring_manager"])
    application = _application(demo, job, applied)

    moved = move_application_stage(
        application_id=application.id,
        target_stage_id=screening.id,
        organization=demo,
        actor=owner,
        membership=membership,
        idempotency_key="owner-1",
        expected_version=application.version,
    )

    assert moved.current_stage_id == screening.id


@pytest.mark.django_db
def test_recruiter_cannot_move_an_application_outside_their_department(demo, scoped):
    job, applied, screening, _, sales, _, _ = scoped
    outsider, membership = _member(
        demo,
        "other.dept@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        department_scope=[str(sales.id)],
    )
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError):
        move_application_stage(
            application_id=application.id,
            target_stage_id=screening.id,
            organization=demo,
            actor=outsider,
            membership=membership,
            idempotency_key="dept-1",
            expected_version=application.version,
        )

    application.refresh_from_db()
    assert application.current_stage_id == applied.id


@pytest.mark.django_db
def test_recruiter_cannot_move_an_application_outside_their_location(demo, scoped):
    job, applied, screening, _, _, _, bandung = scoped
    outsider, membership = _member(
        demo,
        "other.loc@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        location_scope=[str(bandung.id)],
    )
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError):
        move_application_stage(
            application_id=application.id,
            target_stage_id=screening.id,
            organization=demo,
            actor=outsider,
            membership=membership,
            idempotency_key="loc-1",
            expected_version=application.version,
        )

    application.refresh_from_db()
    assert application.current_stage_id == applied.id


# --------------------------------------------------- _check_job_scope in isolation


@pytest.mark.django_db
def test_scope_guard_refuses_a_hiring_manager_who_does_not_own_the_job(demo, admin_user, scoped):
    """The backstop itself. Covered directly because the queryset layer normally fires first."""
    job, *_ = scoped
    peer, membership = _member(
        demo, "guard.hm@example.com", role=OrganizationMembership.Role.HIRING_MANAGER
    )

    with pytest.raises(ValidationError, match="do not have access to this job"):
        _check_job_scope(membership, job)

    # Being on the hiring team is enough, even without owning the requisition.
    JobTeamMember.objects.create(job=job, user=peer, role="INTERVIEWER")
    _check_job_scope(membership, job)


@pytest.mark.django_db
def test_scope_guard_refuses_a_recruiter_outside_their_department(demo, scoped):
    job, _, _, _, sales, _, _ = scoped
    _, membership = _member(
        demo,
        "guard.dept@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        department_scope=[str(sales.id)],
    )

    with pytest.raises(ValidationError, match="outside your department scope"):
        _check_job_scope(membership, job)


@pytest.mark.django_db
def test_scope_guard_refuses_a_recruiter_outside_their_location(demo, scoped):
    job, _, _, _, _, _, bandung = scoped
    _, membership = _member(
        demo,
        "guard.loc@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        location_scope=[str(bandung.id)],
    )

    with pytest.raises(ValidationError, match="outside your location scope"):
        _check_job_scope(membership, job)


# ------------------------------------------------------------------ move-stage rules


@pytest.mark.django_db
def test_a_move_requires_an_idempotency_key(demo, admin_user, scoped):
    """The web form always sends one; the service must still refuse a caller that does not."""
    job, applied, screening, *_ = scoped
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError, match="Idempotency-Key is required"):
        move_application_stage(
            application_id=application.id,
            target_stage_id=screening.id,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
            idempotency_key="",
            expected_version=application.version,
        )


@pytest.mark.django_db
def test_only_active_applications_can_move(demo, admin_user, scoped):
    """A rejected or hired candidate must not re-enter the funnel by a stage move."""
    job, applied, screening, *_ = scoped
    application = _application(demo, job, applied)
    application.status = Application.Status.REJECTED
    application.save(update_fields=["status"])

    with pytest.raises(ValidationError, match="Only active applications"):
        move_application_stage(
            application_id=application.id,
            target_stage_id=screening.id,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
            idempotency_key="inactive-1",
            expected_version=application.version,
        )


@pytest.mark.django_db
def test_the_target_stage_must_belong_to_the_same_job(demo, admin_user, scoped):
    """Otherwise a crafted stage id could pull a candidate into another requisition."""
    job, applied, _, engineering, _, jakarta, _ = scoped
    other_job = _job(demo, admin_user, engineering, jakarta, "Other requisition")
    foreign_stage = _stage(other_job, "Foreign", position=1)
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError, match="does not belong to this job"):
        move_application_stage(
            application_id=application.id,
            target_stage_id=foreign_stage.id,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
            idempotency_key="foreign-1",
            expected_version=application.version,
        )


@pytest.mark.django_db
def test_a_stage_requiring_an_interview_blocks_the_move(demo, admin_user, scoped):
    job, applied, *_ = scoped
    panel = _stage(job, "Panel", position=3, requires_interview=True)
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError, match="Schedule an interview"):
        move_application_stage(
            application_id=application.id,
            target_stage_id=panel.id,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
            idempotency_key="needs-interview",
            expected_version=application.version,
        )


@pytest.mark.django_db
def test_a_stage_requiring_a_scorecard_blocks_the_move(demo, admin_user, scoped):
    job, applied, *_ = scoped
    decided = _stage(job, "Decision", position=4, requires_scorecard=True)
    application = _application(demo, job, applied)

    with pytest.raises(ValidationError, match="Submit the required scorecard"):
        move_application_stage(
            application_id=application.id,
            target_stage_id=decided.id,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
            idempotency_key="needs-scorecard",
            expected_version=application.version,
        )


@pytest.mark.django_db
def test_a_scorecard_gate_opens_once_a_scorecard_is_submitted(demo, admin_user, scoped):
    """The gate must actually open -- a rule that cannot be satisfied is just a dead end."""
    from apps.jobs.models import ScorecardTemplate

    job, applied, *_ = scoped
    decided = _stage(job, "Decision", position=4, requires_scorecard=True)
    application = _application(demo, job, applied)
    template = ScorecardTemplate.objects.create(job=job, name="Decision rubric", version=1)
    interview = Interview.objects.create(
        application=application,
        scorecard_template=template,
        template_snapshot={"version": 1, "criteria": []},
        title="Decision interview",
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=1),
        created_by=admin_user,
    )
    ScorecardSubmission.objects.create(
        interview=interview,
        application=application,
        submitted_by=admin_user,
        template_version=1,
        status=ScorecardSubmission.Status.SUBMITTED,
        submitted_at=timezone.now(),
        locked_at=timezone.now(),
    )

    moved = move_application_stage(
        application_id=application.id,
        target_stage_id=decided.id,
        organization=demo,
        actor=admin_user,
        membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
        idempotency_key="scorecard-ok",
        expected_version=application.version,
    )

    assert moved.current_stage_id == decided.id


@pytest.mark.django_db
def test_entering_the_hired_stage_stamps_the_hire(demo, admin_user, scoped):
    """Hiring is a state change, not just a stage change -- `hired_at` feeds the reports."""
    job, applied, *_ = scoped
    hired = _stage(job, "Hired", position=5, stage_type=StageType.HIRED, terminal=True)
    application = _application(demo, job, applied)

    moved = move_application_stage(
        application_id=application.id,
        target_stage_id=hired.id,
        organization=demo,
        actor=admin_user,
        membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
        idempotency_key="hire-1",
        expected_version=application.version,
    )

    assert moved.status == Application.Status.HIRED
    assert moved.hired_at is not None


# ------------------------------------------------------------------------- rejection


@pytest.mark.django_db
def test_rejection_requires_a_visible_application(demo, admin_user, scoped):
    job, applied, *_ = scoped
    reason = RejectionReason.objects.filter(organization=demo, active=True).first()
    membership = OrganizationMembership.objects.get(organization=demo, user=admin_user)

    with pytest.raises(ValidationError, match="Application not found"):
        reject_application(
            application_id=uuid.uuid4(),
            reason=reason,
            organization=demo,
            actor=admin_user,
            membership=membership,
        )


@pytest.mark.django_db
def test_an_inactive_application_cannot_be_rejected_twice(demo, admin_user, scoped):
    """Re-rejecting would rewrite `rejected_at` and corrupt the funnel's time-to-reject."""
    job, applied, *_ = scoped
    application = _application(demo, job, applied)
    application.status = Application.Status.HIRED
    application.save(update_fields=["status"])
    reason = RejectionReason.objects.filter(organization=demo, active=True).first()

    with pytest.raises(ValidationError, match="Only active applications"):
        reject_application(
            application_id=application.id,
            reason=reason,
            organization=demo,
            actor=admin_user,
            membership=OrganizationMembership.objects.get(organization=demo, user=admin_user),
        )
