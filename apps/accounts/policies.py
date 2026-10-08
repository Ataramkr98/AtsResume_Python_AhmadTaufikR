from __future__ import annotations

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Organization, OrganizationMembership

ROLE_PERMISSIONS = {
    OrganizationMembership.Role.ORG_ADMIN: {"*"},
    OrganizationMembership.Role.HR_ADMIN: {
        "jobs.manage",
        "jobs.publish",
        "candidates.manage",
        "applications.move",
        "applications.reject",
        "interviews.manage",
        "scorecards.submit",
        "scorecards.reopen",
        "reports.view",
        "audit.view",
        "members.invite",
        "tasks.manage",
    },
    OrganizationMembership.Role.RECRUITER: {
        "jobs.manage",
        "jobs.publish",
        "candidates.manage",
        "applications.move",
        "applications.reject",
        "interviews.manage",
        "scorecards.submit",
        "tasks.manage",
    },
    OrganizationMembership.Role.HIRING_MANAGER: {
        "jobs.view_owned",
        "candidates.view_assigned",
        "applications.move",
        "interviews.view",
        "scorecards.submit",
        "tasks.manage",
    },
    OrganizationMembership.Role.INTERVIEWER: {
        "interviews.view_assigned",
        "scorecards.submit",
        "tasks.manage",
    },
    OrganizationMembership.Role.AUDITOR: {"audit.view", "reports.view", "read_only"},
}


def get_membership(user, organization: Organization) -> OrganizationMembership:
    if not user.is_authenticated:
        raise Http404
    membership = OrganizationMembership.objects.filter(
        user=user, organization=organization, active=True, organization__status=Organization.Status.ACTIVE
    ).first()
    if not membership:
        raise Http404
    return membership


def has_permission(membership: OrganizationMembership, permission: str) -> bool:
    permissions = ROLE_PERMISSIONS.get(membership.role, set())
    return "*" in permissions or permission in permissions


def require_permission(membership: OrganizationMembership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise Http404


def organization_context(request, organization_slug: str):
    """Resolve the organization plus the caller's membership, or raise 404.

    Shared by every organization-scoped web view so tenant resolution stays in one place.
    """
    organization = get_object_or_404(Organization, slug=organization_slug)
    return organization, get_membership(request.user, organization)


def can_view_candidate_contact(membership: OrganizationMembership) -> bool:
    """Roles allowed to see decrypted candidate contact details."""
    return membership.role in {
        OrganizationMembership.Role.ORG_ADMIN,
        OrganizationMembership.Role.HR_ADMIN,
        OrganizationMembership.Role.RECRUITER,
    }


def scope_jobs(membership: OrganizationMembership, queryset=None):
    """Apply tenant and role object-level access to a job queryset."""
    from apps.jobs.models import Job

    queryset = queryset if queryset is not None else Job.objects.all()
    queryset = queryset.filter(organization=membership.organization)
    if membership.role == OrganizationMembership.Role.HIRING_MANAGER:
        queryset = queryset.filter(
            Q(hiring_manager=membership.user) | Q(team_members__user=membership.user)
        ).distinct()
    elif membership.role == OrganizationMembership.Role.RECRUITER:
        if membership.department_scope:
            queryset = queryset.filter(department_id__in=membership.department_scope)
        if membership.location_scope:
            queryset = queryset.filter(location_id__in=membership.location_scope)
    elif membership.role == OrganizationMembership.Role.INTERVIEWER:
        queryset = queryset.filter(
            applications__interviews__participants__user=membership.user
        ).distinct()
    return queryset


def scope_applications(membership: OrganizationMembership, queryset=None):
    """Apply tenant and role object-level access to an application queryset."""
    from apps.applications.models import Application

    queryset = queryset if queryset is not None else Application.objects.all()
    queryset = queryset.filter(organization=membership.organization)
    if membership.role == OrganizationMembership.Role.HIRING_MANAGER:
        queryset = queryset.filter(
            Q(job__hiring_manager=membership.user) | Q(job__team_members__user=membership.user)
        ).distinct()
    elif membership.role == OrganizationMembership.Role.RECRUITER:
        if membership.department_scope:
            queryset = queryset.filter(job__department_id__in=membership.department_scope)
        if membership.location_scope:
            queryset = queryset.filter(job__location_id__in=membership.location_scope)
    elif membership.role == OrganizationMembership.Role.INTERVIEWER:
        queryset = queryset.filter(interviews__participants__user=membership.user).distinct()
    return queryset


def scope_candidates(membership: OrganizationMembership, queryset=None):
    """Apply candidate visibility rules without exposing unrelated applications."""
    from apps.candidates.models import Candidate

    queryset = queryset if queryset is not None else Candidate.objects.all()
    queryset = queryset.filter(organization=membership.organization)
    if membership.role == OrganizationMembership.Role.HIRING_MANAGER:
        queryset = queryset.filter(
            Q(applications__job__hiring_manager=membership.user)
            | Q(applications__job__team_members__user=membership.user)
        ).distinct()
    elif membership.role == OrganizationMembership.Role.RECRUITER:
        if membership.department_scope:
            queryset = queryset.filter(
                applications__job__department_id__in=membership.department_scope
            )
        if membership.location_scope:
            queryset = queryset.filter(
                applications__job__location_id__in=membership.location_scope
            )
        queryset = queryset.distinct()
    elif membership.role == OrganizationMembership.Role.INTERVIEWER:
        queryset = queryset.filter(
            applications__interviews__participants__user=membership.user
        ).distinct()
    return queryset


def scope_resumes(membership: OrganizationMembership, queryset=None):
    from apps.candidates.models import Resume

    queryset = queryset if queryset is not None else Resume.objects.all()
    visible_candidates = scope_candidates(membership).values("id")
    return queryset.filter(
        organization=membership.organization,
        candidate_id__in=visible_candidates,
    )


def scope_interviews(membership: OrganizationMembership, queryset=None):
    from apps.interviews.models import Interview

    queryset = queryset if queryset is not None else Interview.objects.all()
    visible_applications = scope_applications(membership).values("id")
    return queryset.filter(application_id__in=visible_applications)


def scope_tasks(membership: OrganizationMembership, queryset=None):
    """Admins see every task in the workspace; everyone else sees their own."""
    from apps.tasks.models import Task

    queryset = queryset if queryset is not None else Task.objects.all()
    queryset = queryset.filter(organization=membership.organization)
    if membership.role in {
        OrganizationMembership.Role.ORG_ADMIN,
        OrganizationMembership.Role.HR_ADMIN,
    }:
        return queryset
    return queryset.filter(
        Q(assignee=membership.user) | Q(created_by=membership.user)
    ).distinct()
