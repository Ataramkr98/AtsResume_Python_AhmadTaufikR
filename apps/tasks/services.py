from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.policies import require_permission, scope_tasks
from apps.audit.models import record_audit

from .models import Task


@transaction.atomic
def create_task(*, organization, actor, membership, data):
    require_permission(membership, "tasks.manage")
    assignee = data.get("assignee")
    if not assignee or not organization.memberships.filter(user=assignee, active=True).exists():
        raise ValidationError("Choose an assignee who belongs to this workspace.")
    application = data.get("application")
    job = data.get("job")
    if application and application.organization_id != organization.id:
        raise ValidationError("Invalid application.")
    if job and job.organization_id != organization.id:
        raise ValidationError("Invalid job.")
    # The API's write serializer accepts `status`, so a task can be created already closed.
    # Stamp `completed_at` here too, or a DONE task created through the API would report no
    # completion time while an identical one moved to DONE would -- the same status meaning two
    # different things depending on how it was reached.
    if data.get("status") == Task.Status.DONE:
        data = {**data, "completed_at": timezone.now()}
    task = Task.objects.create(organization=organization, created_by=actor, **data)
    record_audit(
        organization=organization,
        actor=actor,
        action="task.created",
        resource=task,
        after={"title": task.title, "assignee_id": str(task.assignee_id)},
    )
    return task


@transaction.atomic
def set_task_status(*, task_id, organization, actor, membership, status):
    require_permission(membership, "tasks.manage")
    if status not in Task.Status.values:
        raise ValidationError("Unknown task status.")
    task = scope_tasks(membership, Task.objects.select_for_update()).filter(id=task_id).first()
    if not task:
        raise ValidationError("Task not found.")
    if task.status == status:
        return task
    before = {"status": task.status}
    task.status = status
    task.completed_at = timezone.now() if status == Task.Status.DONE else None
    task.save(update_fields=["status", "completed_at", "updated_at"])
    record_audit(
        organization=organization,
        actor=actor,
        action="task.status_changed",
        resource=task,
        before=before,
        after={"status": task.status},
    )
    return task


@transaction.atomic
def update_task(*, task_id, organization, actor, membership, data):
    """Patch a task, routing any status change through the same completion bookkeeping."""
    require_permission(membership, "tasks.manage")
    task = scope_tasks(membership, Task.objects.select_for_update()).filter(id=task_id).first()
    if not task:
        raise ValidationError("Task not found.")
    before = {"status": task.status}
    touched: list[str] = []
    for field, value in data.items():
        if field == "status":
            if value == task.status:
                continue
            task.status = value
            task.completed_at = timezone.now() if value == Task.Status.DONE else None
            touched += ["status", "completed_at"]
        else:
            setattr(task, field, value)
            touched.append(field)
    if not touched:
        return task
    task.save(update_fields=[*touched, "updated_at"])
    record_audit(
        organization=organization,
        actor=actor,
        action="task.updated",
        resource=task,
        before=before,
        after={"status": task.status, "fields": touched},
    )
    return task
