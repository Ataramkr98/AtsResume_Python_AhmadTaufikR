"""The task list filters and the two task write endpoints (apps/tasks/views.py).

At 81% this was the last product-code thin spot outside the two environment-dependent modules.
The uncovered statements were exactly the interactive surface: the explicit-status, assignee and
text-search filter branches on the list, the whole `task_create` POST handler, and the
`task_status` error paths. These are Phase-5 "search, filter and every form must work" paths, so
they belong in the suite rather than being assumed.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import OrganizationMembership, User
from apps.tasks.models import Task

ORG = "northstar-labs"


@pytest.fixture
def admin_user(demo):
    return User.objects.get(email="demo@example.com")


@pytest.fixture
def auditor(demo):
    """A logged-in client whose role lacks `tasks.manage` (so writes 404)."""
    user = User.objects.create_user(
        "auditor@example.com", "LongPassword123!", full_name="Ada Auditor"
    )
    OrganizationMembership.objects.create(
        organization=demo,
        user=user,
        role=OrganizationMembership.Role.AUDITOR,
        department_scope=[],
        location_scope=[],
    )
    client = Client()
    assert client.login(username="auditor@example.com", password="LongPassword123!")
    return client


def _task(organization, assignee, **overrides):
    defaults = {
        "organization": organization,
        "assignee": assignee,
        "created_by": assignee,
        "title": "Follow up",
    }
    defaults.update(overrides)
    return Task.objects.create(**defaults)


# ------------------------------------------------------------------------------ list filters


@pytest.mark.django_db
def test_task_list_filters_by_explicit_status(demo_client, demo, admin_user):
    """`?status=DONE` must take the explicit-status branch, not the "open" aggregate."""
    _task(demo, admin_user, title="ZZAlpha open", status=Task.Status.OPEN)
    _task(demo, admin_user, title="ZZBeta done", status=Task.Status.DONE)

    response = demo_client.get(reverse("tasks", args=[ORG]), {"status": "DONE", "q": "ZZ"})

    assert response.status_code == 200
    assert b"ZZBeta done" in response.content
    assert b"ZZAlpha open" not in response.content


@pytest.mark.django_db
def test_task_list_filters_by_open_status(demo_client, demo, admin_user):
    """`?status=open` covers both OPEN and IN_PROGRESS."""
    _task(demo, admin_user, title="ZZGamma", status=Task.Status.IN_PROGRESS)
    _task(demo, admin_user, title="ZZDelta", status=Task.Status.DONE)

    response = demo_client.get(reverse("tasks", args=[ORG]), {"status": "open", "q": "ZZ"})

    assert b"ZZGamma" in response.content
    assert b"ZZDelta" not in response.content


@pytest.mark.django_db
def test_task_list_filters_by_assignee_id(demo_client, demo, admin_user):
    """`?assignee=<id>` narrows to one teammate."""
    other = User.objects.create_user("other@example.com", "LongPassword123!", full_name="Otto Other")
    OrganizationMembership.objects.create(
        organization=demo, user=other, role=OrganizationMembership.Role.RECRUITER,
        department_scope=[], location_scope=[],
    )
    _task(demo, admin_user, title="ZZMine")
    _task(demo, other, title="ZZTheirs")

    mine = demo_client.get(reverse("tasks", args=[ORG]), {"assignee": "me", "q": "ZZ"})
    assert b"ZZMine" in mine.content
    assert b"ZZTheirs" not in mine.content

    theirs = demo_client.get(reverse("tasks", args=[ORG]), {"assignee": str(other.id), "q": "ZZ"})
    assert b"ZZTheirs" in theirs.content
    assert b"ZZMine" not in theirs.content


@pytest.mark.django_db
def test_task_list_searches_title_and_description(demo_client, demo, admin_user):
    """`?q=` matches the title or the description."""
    _task(demo, admin_user, title="ZZSearchable title")
    _task(demo, admin_user, title="Ordinary", description="ZZSearchable body")

    response = demo_client.get(reverse("tasks", args=[ORG]), {"q": "ZZSearchable"})

    assert b"ZZSearchable title" in response.content
    assert b"ZZSearchable body" in response.content


# ----------------------------------------------------------------------------- task_create


@pytest.mark.django_db
def test_task_create_get_renders(demo_client, demo, admin_user):
    response = demo_client.get(reverse("task-create", args=[ORG]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_task_create_post_persists_a_task(demo_client, demo, admin_user):
    before = Task.objects.count()

    response = demo_client.post(
        reverse("task-create", args=[ORG]),
        {
            "title": "Call the shortlist",
            "description": "Schedule screens.",
            "assignee": str(admin_user.id),
            "priority": "HIGH",
            "due_at": "",
            "job": "",
            "application": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert b"Task created." in response.content
    assert Task.objects.count() == before + 1
    task = Task.objects.latest("created_at")
    assert task.title == "Call the shortlist"
    assert task.assignee_id == admin_user.id


@pytest.mark.django_db
def test_task_create_post_redisplays_on_invalid_form(demo_client, demo, admin_user):
    before = Task.objects.count()

    response = demo_client.post(
        reverse("task-create", args=[ORG]),
        {"title": "", "assignee": "", "priority": "NORMAL"},  # title + assignee required
    )

    assert response.status_code == 200
    assert Task.objects.count() == before


@pytest.mark.django_db
def test_task_create_refuses_without_tasks_manage(demo_client, demo, admin_user, auditor):
    before = Task.objects.count()

    response = auditor.post(
        reverse("task-create", args=[ORG]),
        {"title": "Nope", "assignee": str(admin_user.id), "priority": "NORMAL"},
    )

    assert response.status_code == 404
    assert Task.objects.count() == before


# ----------------------------------------------------------------------------- task_status


@pytest.mark.django_db
def test_task_status_marks_a_task_done(demo_client, demo, admin_user):
    task = _task(demo, admin_user, title="Close me", status=Task.Status.OPEN)

    response = demo_client.post(
        reverse("task-status", args=[ORG, task.id]), {"status": "DONE"}, follow=True
    )

    assert b"Task updated." in response.content
    task.refresh_from_db()
    assert task.status == Task.Status.DONE
    assert task.completed_at is not None


@pytest.mark.django_db
def test_task_status_rejects_an_unknown_status_value(demo_client, demo, admin_user):
    """A hand-crafted status outside the enum must be a message, not a silent no-op."""
    task = _task(demo, admin_user, title="Leave me", status=Task.Status.OPEN)

    response = demo_client.post(
        reverse("task-status", args=[ORG, task.id]), {"status": "NOT_A_STATUS"}, follow=True
    )

    assert b"Choose a valid task status." in response.content
    task.refresh_from_db()
    assert task.status == Task.Status.OPEN


@pytest.mark.django_db
def test_task_status_handles_an_unknown_task(demo_client, demo, admin_user):
    """A stale task id surfaces as an error message rather than a 500."""
    response = demo_client.post(
        reverse("task-status", args=[ORG, uuid.uuid4()]), {"status": "DONE"}, follow=True
    )

    assert response.status_code == 200
    assert b"Task not found." in response.content
