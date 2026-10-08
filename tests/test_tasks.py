"""Task service and endpoint coverage.

`set_task_status` was the least-exercised live code path in the project. It backs the
"mark done" control on the task list, and it owns the completion bookkeeping -- `completed_at`
stamped when a task is done, cleared when it is reopened -- yet no test had ever called it.
That also left the tenant guard on the endpoint unproven, which is the more serious gap: a
status endpoint that scopes incorrectly is a cross-workspace write.

The guards in `create_task` (assignee must be a member, linked job/application must belong to
the same workspace) were equally untested. They are the only thing standing between a crafted
API payload and a task that points at another tenant's records.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse

from apps.accounts.models import Organization, OrganizationMembership, User
from apps.applications.models import Application
from apps.audit.models import AuditLog
from apps.jobs.models import Job
from apps.tasks.models import Task
from apps.tasks.services import create_task, set_task_status, update_task

ORG = "northstar-labs"
PASSWORD = "LongPassword123!"


def make_member(organization, email, *, role):
    user = User.objects.create_user(email, PASSWORD, full_name="Task Member")
    membership = OrganizationMembership.objects.create(
        organization=organization, user=user, role=role
    )
    return user, membership


@pytest.fixture
def admin(demo):
    """The seeded administrator, who sees every task in the workspace."""
    user = User.objects.get(email="demo@example.com")
    membership = OrganizationMembership.objects.get(organization=demo, user=user)
    return user, membership


@pytest.fixture
def task(demo, admin):
    user, _ = admin
    return Task.objects.create(
        organization=demo, created_by=user, assignee=user, title="Call the candidate"
    )


# --------------------------------------------------------------------- create_task guards


@pytest.mark.django_db
def test_create_task_audits_the_new_task(demo, admin):
    user, membership = admin

    created = create_task(
        organization=demo,
        actor=user,
        membership=membership,
        data={"title": "Send the offer pack", "assignee": user, "priority": Task.Priority.HIGH},
    )

    assert created.organization == demo
    assert created.created_by == user
    assert created.status == Task.Status.OPEN
    assert created.completed_at is None

    event = AuditLog.objects.get(action="task.created", resource_id=str(created.pk))
    assert event.actor == user
    assert event.after["title"] == "Send the offer pack"
    assert event.after["assignee_id"] == str(user.pk)


@pytest.mark.django_db
def test_create_task_stamps_completion_when_created_already_done(demo, admin):
    """The API accepts `status`, so a task can arrive closed -- it must still get a timestamp."""
    user, membership = admin

    created = create_task(
        organization=demo,
        actor=user,
        membership=membership,
        data={"title": "Already handled", "assignee": user, "status": Task.Status.DONE},
    )

    assert created.status == Task.Status.DONE
    assert created.completed_at is not None


@pytest.mark.django_db
def test_create_task_rejects_an_assignee_outside_the_workspace(demo, admin):
    """Otherwise the task would be assigned to someone who cannot see it."""
    user, membership = admin
    outsider = User.objects.create_user("outsider@example.com", PASSWORD, full_name="Outsider")

    with pytest.raises(ValidationError, match="Choose an assignee"):
        create_task(
            organization=demo,
            actor=user,
            membership=membership,
            data={"title": "Nope", "assignee": outsider},
        )


@pytest.mark.django_db
def test_create_task_rejects_an_application_from_another_workspace(demo, admin):
    """A crafted payload must not be able to link a task across tenants."""
    user, membership = admin
    other = Organization.objects.create(name="Other Co", slug="other-co")
    other_membership = OrganizationMembership.objects.create(
        organization=other, user=user, role=OrganizationMembership.Role.ORG_ADMIN
    )
    foreign_application = Application.objects.filter(organization=demo).first()
    assert foreign_application is not None, "seed_demo should have created applications"

    with pytest.raises(ValidationError, match="Invalid application"):
        create_task(
            organization=other,
            actor=user,
            membership=other_membership,
            data={"title": "Cross tenant", "assignee": user, "application": foreign_application},
        )


@pytest.mark.django_db
def test_create_task_rejects_a_job_from_another_workspace(demo, admin):
    user, membership = admin
    other = Organization.objects.create(name="Other Co", slug="other-co")
    other_membership = OrganizationMembership.objects.create(
        organization=other, user=user, role=OrganizationMembership.Role.ORG_ADMIN
    )
    foreign_job = Job.objects.filter(organization=demo).first()
    assert foreign_job is not None, "seed_demo should have created jobs"

    with pytest.raises(ValidationError, match="Invalid job"):
        create_task(
            organization=other,
            actor=user,
            membership=other_membership,
            data={"title": "Cross tenant", "assignee": user, "job": foreign_job},
        )


@pytest.mark.django_db
def test_create_task_requires_the_manage_permission(demo):
    """An auditor can read the workspace but must not be able to create work in it."""
    auditor, membership = make_member(
        demo, "auditor@example.com", role=OrganizationMembership.Role.AUDITOR
    )

    with pytest.raises(Http404):
        create_task(
            organization=demo,
            actor=auditor,
            membership=membership,
            data={"title": "Should not exist", "assignee": auditor},
        )


# ------------------------------------------------------------------ set_task_status


@pytest.mark.django_db
def test_set_task_status_marks_a_task_done(demo, admin, task):
    user, membership = admin

    updated = set_task_status(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        status=Task.Status.DONE,
    )

    assert updated.status == Task.Status.DONE
    assert updated.completed_at is not None

    event = AuditLog.objects.get(action="task.status_changed", resource_id=str(task.pk))
    assert event.before == {"status": Task.Status.OPEN}
    assert event.after == {"status": Task.Status.DONE}


@pytest.mark.django_db
def test_set_task_status_clears_completion_when_reopened(demo, admin, task):
    """Reopening has to undo the stamp, or a reopened task still looks finished."""
    user, membership = admin
    set_task_status(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        status=Task.Status.DONE,
    )

    reopened = set_task_status(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        status=Task.Status.IN_PROGRESS,
    )

    assert reopened.status == Task.Status.IN_PROGRESS
    assert reopened.completed_at is None


@pytest.mark.django_db
def test_set_task_status_is_a_noop_when_already_at_that_status(demo, admin, task):
    """Re-submitting the same status must not write a second audit entry."""
    user, membership = admin

    result = set_task_status(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        status=Task.Status.OPEN,
    )

    assert result.pk == task.pk
    assert not AuditLog.objects.filter(action="task.status_changed", resource_id=str(task.pk)).exists()


@pytest.mark.django_db
def test_set_task_status_rejects_an_unknown_status(demo, admin, task):
    user, membership = admin

    with pytest.raises(ValidationError, match="Unknown task status"):
        set_task_status(
            task_id=task.id,
            organization=demo,
            actor=user,
            membership=membership,
            status="NOT_A_STATUS",
        )


@pytest.mark.django_db
def test_set_task_status_cannot_reach_another_workspaces_task(demo, admin):
    """The scoped lookup is the tenant boundary; an unknown id must look like a missing task."""
    user, membership = admin
    other = Organization.objects.create(name="Other Co", slug="other-co")
    foreign_task = Task.objects.create(
        organization=other, created_by=user, assignee=user, title="Not yours"
    )

    with pytest.raises(ValidationError, match="Task not found"):
        set_task_status(
            task_id=foreign_task.id,
            organization=demo,
            actor=user,
            membership=membership,
            status=Task.Status.DONE,
        )

    foreign_task.refresh_from_db()
    assert foreign_task.status == Task.Status.OPEN


@pytest.mark.django_db
def test_set_task_status_requires_the_manage_permission(demo, task):
    auditor, membership = make_member(
        demo, "auditor2@example.com", role=OrganizationMembership.Role.AUDITOR
    )

    with pytest.raises(Http404):
        set_task_status(
            task_id=task.id,
            organization=demo,
            actor=auditor,
            membership=membership,
            status=Task.Status.DONE,
        )


# ------------------------------------------------------------------------ update_task


@pytest.mark.django_db
def test_update_task_patches_a_single_field(demo, admin, task):
    user, membership = admin

    updated = update_task(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        data={"title": "Call the candidate back"},
    )

    assert updated.title == "Call the candidate back"
    assert updated.status == Task.Status.OPEN
    event = AuditLog.objects.get(action="task.updated", resource_id=str(task.pk))
    assert event.after["fields"] == ["title"]


@pytest.mark.django_db
def test_update_task_routes_a_status_change_through_the_completion_bookkeeping(demo, admin, task):
    user, membership = admin

    updated = update_task(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        data={"status": Task.Status.DONE},
    )

    assert updated.status == Task.Status.DONE
    assert updated.completed_at is not None


@pytest.mark.django_db
def test_update_task_writes_nothing_when_nothing_changed(demo, admin, task):
    """A patch that changes nothing must not produce an audit entry claiming it did."""
    user, membership = admin

    update_task(
        task_id=task.id,
        organization=demo,
        actor=user,
        membership=membership,
        data={"status": task.status},
    )

    assert not AuditLog.objects.filter(action="task.updated", resource_id=str(task.pk)).exists()


@pytest.mark.django_db
def test_update_task_cannot_reach_another_workspaces_task(demo, admin):
    user, membership = admin
    other = Organization.objects.create(name="Other Co", slug="other-co")
    foreign_task = Task.objects.create(
        organization=other, created_by=user, assignee=user, title="Not yours"
    )

    with pytest.raises(ValidationError, match="Task not found"):
        update_task(
            task_id=foreign_task.id,
            organization=demo,
            actor=user,
            membership=membership,
            data={"title": "Hijacked"},
        )

    foreign_task.refresh_from_db()
    assert foreign_task.title == "Not yours"


# ------------------------------------------------------------------- status endpoint


@pytest.mark.django_db
def test_task_status_endpoint_moves_the_task_and_redirects(demo_client, demo, task):
    response = demo_client.post(
        reverse("task-status", args=[ORG, task.id]), {"status": Task.Status.DONE}, follow=True
    )

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.status == Task.Status.DONE
    assert task.completed_at is not None
    # `render()` returns a plain HttpResponse, so there is no `response.context` to inspect --
    # the message has to be asserted where the user actually sees it.
    assert b"Task updated." in response.content


@pytest.mark.django_db
def test_task_status_endpoint_rejects_an_invalid_status(demo_client, task):
    """The form is the gate; a bad value must bounce with an error, not a 500."""
    response = demo_client.post(
        reverse("task-status", args=[ORG, task.id]), {"status": "NOT_A_STATUS"}, follow=True
    )

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.status == Task.Status.OPEN
    assert b"Choose a valid task status." in response.content


@pytest.mark.django_db
def test_task_status_endpoint_is_post_only(demo_client, task):
    """A GET must not mutate anything."""
    response = demo_client.get(reverse("task-status", args=[ORG, task.id]))

    assert response.status_code == 405
    task.refresh_from_db()
    assert task.status == Task.Status.OPEN
