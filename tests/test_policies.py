"""Tenant isolation and object-level scoping (`apps/accounts/policies.py`).

This module is the security boundary. Every list, detail view and API endpoint funnels through
one of the `scope_*` helpers, so a defect here is not a bug in a feature -- it is one
workspace reading another workspace's candidates, or a hiring manager reading a requisition
that is not theirs.

The interviewer branch was already covered by `test_core.py`. The branches that were *not*
covered at all were the hiring-manager and recruiter ones -- the two roles that carry
object-level rules rather than a simple yes/no. Those are the interesting ones, because they
are the easiest to get subtly wrong: an empty `department_scope` must mean "everything", not
"nothing", and a hiring manager must not see a peer's requisition.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404

from apps.accounts.models import Organization, OrganizationMembership
from apps.accounts.policies import (
    can_view_candidate_contact,
    get_membership,
    has_permission,
    require_permission,
    scope_applications,
    scope_candidates,
    scope_jobs,
    scope_tasks,
)
from apps.candidates.models import Candidate
from apps.jobs.models import Department, Job, Location, PipelineStage, PipelineTemplate, StageType
from apps.tasks.models import Task

ORG = "northstar-labs"


def _member(organization, email, *, role, department_scope=None, location_scope=None):
    from apps.accounts.models import User

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
    pipeline = PipelineTemplate.objects.create(organization=organization, name=f"P-{title}")
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


def _candidate(organization, job, stage, email, first="Cand"):
    candidate = Candidate.objects.create(
        organization=organization,
        first_name=first,
        last_name="Person",
        city="Remote",
        country_code="ID",
        total_experience_months=12,
        education_level="bachelor",
    )
    candidate.set_email(email)
    from apps.applications.models import Application

    application = Application.objects.create(
        organization=organization, job=job, candidate=candidate, current_stage=stage, source="MANUAL"
    )
    return candidate, application


@pytest.fixture
def two_departments(demo, admin_user):
    """Two departments and two locations, so scoping has something to separate.

    Names are suffixed per test: `Department` and `Location` are unique per (organization,
    name) and the demo seeder already owns the obvious ones.
    """
    tag = uuid.uuid4().hex[:6]
    engineering = Department.objects.create(organization=demo, name=f"Engineering-{tag}")
    sales = Department.objects.create(organization=demo, name=f"Sales-{tag}")
    jakarta = _location(demo, f"Jakarta-{tag}")
    bandung = _location(demo, f"Bandung-{tag}")
    return engineering, sales, jakarta, bandung


# ----------------------------------------------------------------- hiring manager scoping


@pytest.mark.django_db
def test_hiring_manager_sees_only_their_own_requisitions(demo, admin_user, two_departments):
    """A hiring manager must not read a peer's requisition -- object-level, not role-level."""
    engineering, _, jakarta, _ = two_departments
    peer, _ = _member(demo, "peer.manager@example.com", role=OrganizationMembership.Role.HIRING_MANAGER)
    mine = _job(demo, peer, engineering, jakarta, "Mine")
    theirs = _job(demo, admin_user, engineering, jakarta, "Theirs")

    visible = scope_jobs(peer.memberships.get(organization=demo))

    assert list(visible) == [mine]
    assert theirs not in visible


@pytest.mark.django_db
def test_hiring_manager_sees_only_applications_on_their_requisitions(demo, admin_user, two_departments):
    engineering, _, jakarta, _ = two_departments
    manager, membership = _member(
        demo, "hm.applications@example.com", role=OrganizationMembership.Role.HIRING_MANAGER
    )
    my_job = _job(demo, manager, engineering, jakarta, "HM job")
    their_job = _job(demo, admin_user, engineering, jakarta, "Other job")
    my_stage = PipelineStage.objects.create(
        job=my_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    their_stage = PipelineStage.objects.create(
        job=their_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    _, mine = _candidate(demo, my_job, my_stage, "hm.mine@example.com")
    _, theirs = _candidate(demo, their_job, their_stage, "hm.theirs@example.com")

    visible = scope_applications(membership)

    assert list(visible) == [mine]
    assert theirs not in visible


@pytest.mark.django_db
def test_hiring_manager_sees_only_candidates_on_their_requisitions(demo, admin_user, two_departments):
    engineering, _, jakarta, _ = two_departments
    manager, membership = _member(
        demo, "hm.candidates@example.com", role=OrganizationMembership.Role.HIRING_MANAGER
    )
    my_job = _job(demo, manager, engineering, jakarta, "HM cand job")
    their_job = _job(demo, admin_user, engineering, jakarta, "Other cand job")
    my_stage = PipelineStage.objects.create(
        job=my_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    their_stage = PipelineStage.objects.create(
        job=their_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    mine, _ = _candidate(demo, my_job, my_stage, "hmc.mine@example.com")
    theirs, _ = _candidate(demo, their_job, their_stage, "hmc.theirs@example.com")

    visible = scope_candidates(membership)

    assert mine in visible
    assert theirs not in visible


# --------------------------------------------------------------------- recruiter scoping


@pytest.mark.django_db
def test_recruiter_department_scope_narrows_jobs(demo, admin_user, two_departments):
    """A scoped recruiter sees only their department; an unscoped one sees the workspace."""
    engineering, sales, jakarta, _ = two_departments
    eng_job = _job(demo, admin_user, engineering, jakarta, "Eng role")
    sales_job = _job(demo, admin_user, sales, jakarta, "Sales role")

    scoped, membership = _member(
        demo,
        "scoped.recruiter@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        department_scope=[str(engineering.id)],
    )

    visible = scope_jobs(membership)
    assert eng_job in visible
    assert sales_job not in visible

    # An empty scope must mean "the whole workspace", not "nothing" -- the classic JSONField
    # scoping bug, and the reason this branch is worth a test at all.
    membership.department_scope = []
    membership.save(update_fields=["department_scope"])
    unscoped = scope_jobs(membership)
    assert eng_job in unscoped
    assert sales_job in unscoped


@pytest.mark.django_db
def test_recruiter_location_scope_narrows_jobs(demo, admin_user, two_departments):
    engineering, _, jakarta, bandung = two_departments
    jakarta_job = _job(demo, admin_user, engineering, jakarta, "Jakarta role")
    bandung_job = _job(demo, admin_user, engineering, bandung, "Bandung role")

    _, membership = _member(
        demo,
        "location.recruiter@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        location_scope=[str(bandung.id)],
    )

    visible = scope_jobs(membership)
    assert list(visible) == [bandung_job]
    assert jakarta_job not in visible


@pytest.mark.django_db
def test_recruiter_scope_applies_to_applications_and_candidates(demo, admin_user, two_departments):
    """The same scope must hold on every list, or a recruiter leaks data by changing tabs."""
    engineering, sales, jakarta, _ = two_departments
    eng_job = _job(demo, admin_user, engineering, jakarta, "Eng app role")
    sales_job = _job(demo, admin_user, sales, jakarta, "Sales app role")
    eng_stage = PipelineStage.objects.create(
        job=eng_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    sales_stage = PipelineStage.objects.create(
        job=sales_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    eng_candidate, eng_application = _candidate(demo, eng_job, eng_stage, "eng.cand@example.com")
    sales_candidate, sales_application = _candidate(demo, sales_job, sales_stage, "sales.cand@example.com")

    _, membership = _member(
        demo,
        "dept.recruiter@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        department_scope=[str(engineering.id)],
    )

    assert list(scope_applications(membership)) == [eng_application]
    assert sales_application not in scope_applications(membership)
    assert eng_candidate in scope_candidates(membership)
    assert sales_candidate not in scope_candidates(membership)


@pytest.mark.django_db
def test_recruiter_location_scope_narrows_applications_and_candidates(demo, admin_user, two_departments):
    """Location scope must bind on every list, not just jobs -- scope leaking is data leaking."""
    engineering, _, jakarta, bandung = two_departments
    jakarta_job = _job(demo, admin_user, engineering, jakarta, "Jakarta app role")
    bandung_job = _job(demo, admin_user, engineering, bandung, "Bandung app role")
    jakarta_stage = PipelineStage.objects.create(
        job=jakarta_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    bandung_stage = PipelineStage.objects.create(
        job=bandung_job, name="Applied", stage_type=StageType.APPLIED, position=1, active=True
    )
    jakarta_candidate, jakarta_application = _candidate(
        demo, jakarta_job, jakarta_stage, "jakarta.cand@example.com"
    )
    bandung_candidate, bandung_application = _candidate(
        demo, bandung_job, bandung_stage, "bandung.cand@example.com"
    )

    _, membership = _member(
        demo,
        "loc.apps.recruiter@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        location_scope=[str(bandung.id)],
    )

    assert bandung_application in scope_applications(membership)
    assert jakarta_application not in scope_applications(membership)
    assert bandung_candidate in scope_candidates(membership)
    assert jakarta_candidate not in scope_candidates(membership)


# ------------------------------------------------------------------------- other helpers


@pytest.mark.django_db
def test_get_membership_rejects_anonymous_and_inactive_members(demo):
    """An unauthenticated caller must get a 404, not a 403 -- the org must not be confirmable."""
    with pytest.raises(Http404):
        get_membership(AnonymousUser(), demo)

    user, membership = _member(demo, "inactive@example.com", role=OrganizationMembership.Role.RECRUITER)
    membership.active = False
    membership.save(update_fields=["active"])

    with pytest.raises(Http404):
        get_membership(user, demo)


@pytest.mark.django_db
def test_require_permission_hides_forbidden_actions(demo):
    """A missing permission reads as 'not found', so it cannot be used to probe for resources."""
    _, auditor = _member(demo, "auditor@example.com", role=OrganizationMembership.Role.AUDITOR)

    assert has_permission(auditor, "audit.view") is True
    assert has_permission(auditor, "jobs.publish") is False
    with pytest.raises(Http404):
        require_permission(auditor, "jobs.publish")


@pytest.mark.django_db
def test_can_view_candidate_contact_excludes_interviewers(demo):
    """Contact details are PII; an interviewer needs the panel, not the phone number."""
    _, recruiter = _member(demo, "contact.recruiter@example.com", role=OrganizationMembership.Role.RECRUITER)
    _, interviewer = _member(demo, "contact.interviewer@example.com", role=OrganizationMembership.Role.INTERVIEWER)

    assert can_view_candidate_contact(recruiter) is True
    assert can_view_candidate_contact(interviewer) is False


@pytest.mark.django_db
def test_non_admin_sees_only_their_own_tasks(demo, admin_user):
    """Admins see the whole workspace's task list; everyone else sees their own work."""
    recruiter, membership = _member(
        demo, "tasks.recruiter@example.com", role=OrganizationMembership.Role.RECRUITER
    )
    mine = Task.objects.create(
        organization=demo, title="Mine", created_by=recruiter, assignee=recruiter
    )
    theirs = Task.objects.create(
        organization=demo, title="Theirs", created_by=admin_user, assignee=admin_user
    )

    admin_membership = OrganizationMembership.objects.get(organization=demo, user=admin_user)
    admin_visible = scope_tasks(admin_membership)
    assert mine in admin_visible
    assert theirs in admin_visible

    recruiter_visible = scope_tasks(membership)
    assert mine in recruiter_visible
    assert theirs not in recruiter_visible


@pytest.mark.django_db
def test_scoping_never_escapes_the_organization(demo, admin_user, two_departments):
    """The tenant filter is applied before any role rule -- a second org must be invisible."""
    engineering, _, jakarta, _ = two_departments
    foreign = Organization.objects.create(name="Foreign Co", slug=uuid.uuid4().hex[:12])
    foreign_department = Department.objects.create(organization=foreign, name="X")
    foreign_location = _location(foreign, "Elsewhere")
    foreign_job = _job(foreign, admin_user, foreign_department, foreign_location, "Foreign role")
    local_job = _job(demo, admin_user, engineering, jakarta, "Local role")

    _, membership = _member(demo, "tenant.recruiter@example.com", role=OrganizationMembership.Role.RECRUITER)

    visible = scope_jobs(membership)
    assert local_job in visible
    assert foreign_job not in visible
