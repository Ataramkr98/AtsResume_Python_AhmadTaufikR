from __future__ import annotations

import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.policies import (
    can_view_candidate_contact,
    organization_context,
    require_permission,
)
from apps.audit.models import record_audit

from . import services


def _render(request, template, context, *, status=200):
    context.setdefault("now", timezone.now())
    return render(request, template, context, using="jinja2", status=status)


def _chart_payload(report: dict) -> dict:
    """Chart-ready shapes handed to Chart.js through the template."""
    return {
        "funnel": {
            "labels": [row["stage"] for row in report["funnel"]],
            "values": [row["count"] for row in report["funnel"]],
        },
        "timeInStage": {
            "labels": [row["stage"] for row in report["time_in_stage"]],
            "values": [row["avg_days"] for row in report["time_in_stage"]],
        },
        "sources": {
            "labels": [row["source"] for row in report["source_quality"]],
            "values": [row["total"] for row in report["source_quality"]],
            "hires": [row["hired"] for row in report["source_quality"]],
        },
    }


@login_required
def reports_dashboard(request, organization_slug):
    organization, membership = organization_context(request, organization_slug)
    require_permission(membership, "reports.view")
    report = services.build_report(membership, request.GET.get("range"))
    return _render(
        request,
        "reports/index.html",
        {
            "organization": organization,
            "membership": membership,
            "active_nav": "reports",
            "report": report,
            "chart_data": _chart_payload(report),
            "range_options": services.RANGE_OPTIONS,
            "can_export": True,
        },
    )


@login_required
def export_applications_csv(request, organization_slug):
    """Stream an application export and record it in the audit log.

    Contact details follow the same role rule as the candidate profile, so an auditor
    with reporting access does not gain PII access through the export path.
    """
    organization, membership = organization_context(request, organization_slug)
    require_permission(membership, "reports.view")
    token, since = services.resolve_range(request.GET.get("range"))
    queryset = (
        services.applications_in_range(membership, since)
        .select_related("candidate", "job", "current_stage", "assigned_recruiter")
        .order_by("-applied_at")
    )
    include_contact = can_view_candidate_contact(membership)
    record_audit(
        organization=organization,
        actor=request.user,
        action="report.exported",
        resource=organization,
        after={"report": "applications", "range": token, "rows": queryset.count()},
        request=request,
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="applications-{token}-{timezone.now():%Y%m%d}.csv"'
    )
    writer = csv.writer(response)
    header = [
        "Application ID",
        "Candidate",
        "Job",
        "Department",
        "Stage",
        "Status",
        "Source",
        "Applied at",
        "Days in stage",
        "Match score",
        "Assigned recruiter",
    ]
    if include_contact:
        header += ["Email", "Phone"]
    writer.writerow(header)

    now = timezone.now()
    for application in queryset.iterator():
        row = [
            application.id,
            str(application.candidate),
            application.job.title,
            application.job.department.name,
            application.current_stage.name,
            application.get_status_display(),
            application.source,
            application.applied_at.isoformat(),
            (now - application.last_stage_changed_at).days,
            getattr(getattr(application, "match_score", None), "total_score", ""),
            str(application.assigned_recruiter or ""),
        ]
        if include_contact:
            row += [application.candidate.primary_email, application.candidate.primary_phone]
        writer.writerow(row)
    return response
