from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.policies import (
    has_permission,
    organization_context,
    require_permission,
    scope_applications,
    scope_jobs,
    scope_tasks,
)
from apps.common.pagination import paginate
from apps.jobs.models import Job

from .forms import TaskForm, TaskStatusForm
from .models import Task
from .services import create_task, set_task_status

OPEN_STATUSES = [Task.Status.OPEN, Task.Status.IN_PROGRESS]


def _render(request, template, context, *, status=200):
    context.setdefault("now", timezone.now())
    return render(request, template, context, using="jinja2", status=status)


@login_required
def task_list(request, organization_slug):
    organization, membership = organization_context(request, organization_slug)
    visible = scope_tasks(
        membership,
        Task.objects.select_related("assignee", "job", "application__candidate", "created_by"),
    )
    tasks = visible
    status_filter = request.GET.get("status", "")
    assignee_filter = request.GET.get("assignee", "")
    query = request.GET.get("q", "").strip()
    if status_filter == "open":
        tasks = tasks.filter(status__in=OPEN_STATUSES)
    elif status_filter:
        tasks = tasks.filter(status=status_filter)
    if assignee_filter == "me":
        tasks = tasks.filter(assignee=request.user)
    elif assignee_filter:
        tasks = tasks.filter(assignee_id=assignee_filter)
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(description__icontains=query))

    page, querystring = paginate(request, tasks, per_page=15)
    return _render(
        request,
        "tasks/list.html",
        {
            "organization": organization,
            "membership": membership,
            "active_nav": "tasks",
            "page": page,
            "querystring": querystring,
            "status_filter": status_filter,
            "assignee_filter": assignee_filter,
            "query": query,
            "assignees": organization.memberships.filter(active=True).select_related("user"),
            "counts": {
                "open": visible.filter(status__in=OPEN_STATUSES).count(),
                "overdue": visible.filter(
                    status__in=OPEN_STATUSES, due_at__lt=timezone.now()
                ).count(),
                "mine": visible.filter(assignee=request.user, status__in=OPEN_STATUSES).count(),
            },
            "can_manage": has_permission(membership, "tasks.manage"),
        },
    )


@login_required
def task_create(request, organization_slug):
    organization, membership = organization_context(request, organization_slug)
    require_permission(membership, "tasks.manage")
    jobs = scope_jobs(membership, Job.objects.filter(deleted_at__isnull=True))
    applications = scope_applications(membership)
    if request.method == "POST":
        form = TaskForm(
            request.POST, organization=organization, jobs=jobs, applications=applications
        )
        if form.is_valid():
            try:
                create_task(
                    organization=organization,
                    actor=request.user,
                    membership=membership,
                    data=form.cleaned_data,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Task created.")
                return redirect("tasks", organization_slug=organization.slug)
    else:
        form = TaskForm(organization=organization, jobs=jobs, applications=applications)
    return _render(
        request,
        "tasks/form.html",
        {
            "organization": organization,
            "membership": membership,
            "active_nav": "tasks",
            "form": form,
        },
    )


@login_required
@require_POST
def task_status(request, organization_slug, task_id):
    organization, membership = organization_context(request, organization_slug)
    form = TaskStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid task status.")
        return redirect("tasks", organization_slug=organization.slug)
    try:
        set_task_status(
            task_id=task_id,
            organization=organization,
            actor=request.user,
            membership=membership,
            status=form.cleaned_data["status"],
        )
        messages.success(request, "Task updated.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("tasks", organization_slug=organization.slug)
