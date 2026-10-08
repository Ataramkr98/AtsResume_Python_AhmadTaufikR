"""Tenant-scoped hiring analytics.

Every query starts from the policy-scoped querysets in ``apps.accounts.policies`` so a report can
never widen what a role is allowed to see. Metric definitions are documented inline because the
numbers are shown to hiring managers.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.accounts.policies import scope_applications, scope_interviews, scope_jobs
from apps.applications.models import Application, ApplicationStageHistory
from apps.candidates.models import Resume
from apps.interviews.models import Interview, InterviewParticipant, ScorecardSubmission
from apps.jobs.models import Job, StageType

# Canonical funnel order. CUSTOM stages are appended after the known types so the
# conversion chain stays meaningful even for bespoke pipelines.
STAGE_TYPE_ORDER = [
    StageType.APPLIED,
    StageType.SCREENING,
    StageType.INTERVIEW,
    StageType.ASSESSMENT,
    StageType.OFFER,
    StageType.HIRED,
    StageType.CUSTOM,
]

RANGE_OPTIONS = [
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
    ("365", "Last 12 months"),
    ("all", "All time"),
]
DEFAULT_RANGE = "90"


def resolve_range(value: str | None) -> tuple[str, timedelta | None]:
    """Map a range token to a lookback window. Unknown tokens fall back to the default."""
    token = (value or "").strip()
    if token not in dict(RANGE_OPTIONS):
        token = DEFAULT_RANGE
    if token == "all":
        return token, None
    return token, timedelta(days=int(token))


def range_label(token: str) -> str:
    return dict(RANGE_OPTIONS).get(token, dict(RANGE_OPTIONS)[DEFAULT_RANGE])


def _applications(membership: OrganizationMembership, since: timedelta | None):
    queryset = scope_applications(membership, Application.objects.all())
    if since is not None:
        queryset = queryset.filter(applied_at__gte=timezone.now() - since)
    return queryset


def _interviews(membership: OrganizationMembership, since: timedelta | None):
    queryset = scope_interviews(membership, Interview.objects.all())
    if since is not None:
        queryset = queryset.filter(start_at__gte=timezone.now() - since)
    return queryset


def applications_in_range(membership: OrganizationMembership, since: timedelta | None):
    """Public entry point for callers that need the scoped application queryset itself."""
    return _applications(membership, since)


def summary(membership: OrganizationMembership, since: timedelta | None) -> dict:
    """Headline KPIs. Each value is a live aggregate, never a placeholder."""
    applications = _applications(membership, since)
    interviews = _interviews(membership, since)
    jobs = scope_jobs(membership, Job.objects.filter(deleted_at__isnull=True))
    resumes = Resume.objects.filter(
        organization=membership.organization,
        candidate_id__in=applications.values("candidate_id"),
    )
    resume_total = resumes.count()
    parsed = resumes.filter(
        parser_status__in=[Resume.ParserStatus.COMPLETED, Resume.ParserStatus.REVIEW_REQUIRED]
    ).count()
    return {
        "open_jobs": jobs.filter(status=Job.Status.OPEN).count(),
        "active_applications": applications.filter(status=Application.Status.ACTIVE).count(),
        "interviews_scheduled": interviews.filter(status=Interview.Status.SCHEDULED).count(),
        "hired": applications.filter(status=Application.Status.HIRED).count(),
        "rejected": applications.filter(status=Application.Status.REJECTED).count(),
        "resumes_total": resume_total,
        "resumes_parsed": parsed,
        "parse_success_rate": round(parsed / resume_total * 100, 1) if resume_total else None,
    }


def hiring_funnel(membership: OrganizationMembership, since: timedelta | None) -> list[dict]:
    """Distinct applications that ever reached each stage type, with step conversion.

    The Applied cohort is every application in range; deeper cohorts come from
    ``ApplicationStageHistory`` so a candidate who skipped or was moved back is still counted once.
    """
    applications = _applications(membership, since)
    total = applications.count()
    reached = dict(
        ApplicationStageHistory.objects.filter(application__in=applications)
        .values_list("to_stage__stage_type")
        .annotate(total=Count("application", distinct=True))
    )
    labels = dict(StageType.choices)
    rows: list[dict] = []
    previous = None
    for stage_type in STAGE_TYPE_ORDER:
        if stage_type == StageType.APPLIED:
            count = total
        else:
            count = reached.get(stage_type, 0)
        if stage_type == StageType.CUSTOM and not count:
            continue
        if not count and stage_type != StageType.APPLIED:
            continue
        rows.append(
            {
                "stage": labels.get(stage_type, stage_type),
                "count": count,
                "of_total": round(count / total * 100, 1) if total else 0,
                "step_conversion": round(count / previous * 100, 1) if previous else None,
            }
        )
        previous = count
    return rows


def time_in_stage(membership: OrganizationMembership, since: timedelta | None) -> list[dict]:
    """Average time spent in each stage before moving on.

    ``duration_previous_stage_seconds`` is recorded on the move, so it describes the stage the
    application left — therefore we group by ``from_stage``.
    """
    history = ApplicationStageHistory.objects.filter(
        application__in=_applications(membership, since),
        from_stage__isnull=False,
        duration_previous_stage_seconds__gt=0,
    )
    rows = (
        history.values("from_stage__name")
        .annotate(avg_seconds=Avg("duration_previous_stage_seconds"), samples=Count("id"))
        .order_by("-avg_seconds")
    )
    return [
        {
            "stage": row["from_stage__name"],
            "avg_days": round(row["avg_seconds"] / 86400, 1),
            "samples": row["samples"],
        }
        for row in rows
    ]


def time_to_hire(membership: OrganizationMembership, since: timedelta | None) -> dict:
    """Average and median days from application to hire, computed over hires only."""
    hired = _applications(membership, since).filter(
        status=Application.Status.HIRED, hired_at__isnull=False
    )
    durations = sorted(
        (row.hired_at - row.applied_at).total_seconds() / 86400
        for row in hired.only("hired_at", "applied_at")
        if row.hired_at and row.applied_at
    )
    if not durations:
        return {"count": 0, "average_days": None, "median_days": None}
    middle = len(durations) // 2
    median = (
        durations[middle]
        if len(durations) % 2
        else (durations[middle - 1] + durations[middle]) / 2
    )
    return {
        "count": len(durations),
        "average_days": round(sum(durations) / len(durations), 1),
        "median_days": round(median, 1),
    }


def source_quality(membership: OrganizationMembership, since: timedelta | None) -> list[dict]:
    """Applications, hires and hire-rate per source, highest volume first."""
    rows = (
        _applications(membership, since)
        .values("source")
        .annotate(
            total=Count("id"),
            hired=Count("id", filter=Q(status=Application.Status.HIRED)),
            active=Count("id", filter=Q(status=Application.Status.ACTIVE)),
            rejected=Count("id", filter=Q(status=Application.Status.REJECTED)),
        )
        .order_by("-total", "source")
    )
    return [
        {
            "source": row["source"].replace("_", " ").title(),
            "total": row["total"],
            "hired": row["hired"],
            "active": row["active"],
            "rejected": row["rejected"],
            "hire_rate": round(row["hired"] / row["total"] * 100, 1) if row["total"] else 0,
        }
        for row in rows
    ]


def interviewer_completion(membership: OrganizationMembership, since: timedelta | None) -> dict:
    """Submitted scorecards versus interview invitations, plus overdue feedback."""
    interviews = _interviews(membership, since)
    interview_ids = interviews.values("id")
    invited = InterviewParticipant.objects.filter(interview_id__in=interview_ids).count()
    submitted = ScorecardSubmission.objects.filter(
        interview_id__in=interview_ids, status=ScorecardSubmission.Status.SUBMITTED
    ).count()
    overdue = (
        interviews.filter(status=Interview.Status.SCHEDULED, end_at__lt=timezone.now())
        .exclude(
            scorecard_submissions__status=ScorecardSubmission.Status.SUBMITTED,
        )
        .count()
    )
    return {
        "interviews": interviews.count(),
        "invited": invited,
        "submitted": submitted,
        "overdue": overdue,
        "completion_rate": round(submitted / invited * 100, 1) if invited else None,
    }


def aging_applications(membership: OrganizationMembership, limit: int = 8) -> list[dict]:
    """Active applications that have sat in their current stage longest."""
    rows = (
        _applications(membership, None)
        .filter(status=Application.Status.ACTIVE)
        .select_related("candidate", "job", "current_stage")
        .order_by("last_stage_changed_at")[:limit]
    )
    now = timezone.now()
    results = []
    for application in rows:
        days = (now - application.last_stage_changed_at).days
        sla_hours = application.current_stage.sla_hours
        results.append(
            {
                "application": application,
                "days_in_stage": days,
                "sla_hours": sla_hours,
                "overdue": bool(sla_hours and days * 24 > sla_hours),
            }
        )
    return results


def build_report(membership: OrganizationMembership, range_token: str | None) -> dict:
    token, since = resolve_range(range_token)
    return {
        "range_token": token,
        "range_label": range_label(token),
        "summary": summary(membership, since),
        "funnel": hiring_funnel(membership, since),
        "time_in_stage": time_in_stage(membership, since),
        "time_to_hire": time_to_hire(membership, since),
        "source_quality": source_quality(membership, since),
        "interviewer_completion": interviewer_completion(membership, since),
        "aging": aging_applications(membership),
    }
