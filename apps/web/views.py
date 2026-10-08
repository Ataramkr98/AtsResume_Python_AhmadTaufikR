from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.forms import InvitationForm, ProfileForm
from apps.accounts.models import Invitation, Organization, OrganizationMembership, User
from apps.accounts.policies import (
    can_view_candidate_contact,
    has_permission,
    organization_context,
    require_permission,
    scope_applications,
    scope_candidates,
    scope_interviews,
    scope_jobs,
    scope_resumes,
    scope_tasks,
)
from apps.applications.forms import MoveStageForm, RejectApplicationForm
from apps.applications.models import Application, RejectionReason
from apps.applications.services import move_application_stage, reject_application
from apps.audit.models import AuditLog, record_audit
from apps.candidates.forms import CandidateForm, PublicApplicationForm
from apps.candidates.models import Candidate, DuplicateSuggestion, Resume
from apps.candidates.services import (
    create_candidate,
    create_public_application,
    merge_candidates,
    update_candidate,
)
from apps.common.pagination import paginate
from apps.interviews.forms import InterviewForm
from apps.interviews.models import Interview, ScorecardSubmission
from apps.interviews.services import (
    reopen_scorecard,
    save_scorecard,
    schedule_interview,
    set_interview_status,
)
from apps.jobs.forms import JobForm, JobTransitionForm
from apps.jobs.models import Job
from apps.jobs.services import create_job, transition_job, update_job
from apps.reports import services as report_services
from apps.tasks.models import Task

# Tasks that still require attention. "Open" for KPI purposes means anything not yet closed.
OPEN_TASK_STATUSES = [Task.Status.OPEN, Task.Status.IN_PROGRESS]


def _organization_context(request, organization_slug):
    return organization_context(request, organization_slug)


def _render(request, template, context=None, *, status=200):
    context = context or {}
    context.setdefault("now", timezone.now())
    return render(request, template, context, using="jinja2", status=status)


def landing(request):
    """Public marketing entry point backed by real, live figures."""
    if request.user.is_authenticated:
        return redirect("app-entry")
    active_organizations = Organization.objects.filter(status=Organization.Status.ACTIVE)
    demo_organization = active_organizations.filter(slug="northstar-labs").first()
    demo_email = settings.DEMO_EMAIL
    demo_password = ""
    if demo_email and User.objects.filter(email__iexact=demo_email, is_active=True).exists():
        demo_password = settings.DEMO_PASSWORD
    return _render(
        request,
        "public/landing.html",
        {
            "open_roles": Job.objects.filter(
                status=Job.Status.OPEN,
                deleted_at__isnull=True,
                organization__status=Organization.Status.ACTIVE,
            ).count(),
            "candidate_count": Candidate.objects.filter(deleted_at__isnull=True).count(),
            "organization_count": active_organizations.count(),
            "careers_url": (
                reverse("public-jobs", args=[demo_organization.slug]) if demo_organization else ""
            ),
            "demo_email": demo_email if demo_password else "",
            "demo_password": demo_password,
        },
    )


def not_found(request, exception=None):
    return render(request, "404.html", using="jinja2", status=404)


def server_error(request):
    return render(request, "500.html", using="jinja2", status=500)


def health(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    from django.db import connection

    try:
        connection.ensure_connection()
        return JsonResponse({"status": "ready", "database": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable", "database": "error"}, status=503)


def public_jobs(request, organization_slug):
    organization = get_object_or_404(Organization, slug=organization_slug, status=Organization.Status.ACTIVE)
    jobs = Job.objects.filter(
        organization=organization,
        status=Job.Status.OPEN,
        deleted_at__isnull=True,
    ).select_related("department", "location")
    query = request.GET.get("q", "").strip()
    if query:
        jobs = jobs.filter(Q(title__icontains=query) | Q(department__name__icontains=query))
    return _render(request, "careers/jobs.html", {"organization": organization, "jobs": jobs, "query": query})


def public_job_detail(request, organization_slug, job_slug):
    job = get_object_or_404(
        Job.objects.select_related("organization", "department", "location"),
        organization__slug=organization_slug,
        organization__status=Organization.Status.ACTIVE,
        slug=job_slug,
        status=Job.Status.OPEN,
        deleted_at__isnull=True,
    )
    return _render(request, "careers/job_detail.html", {"organization": job.organization, "job": job})


def public_apply(request, organization_slug, job_slug):
    job = get_object_or_404(
        Job.objects.select_related("organization", "department", "location"),
        organization__slug=organization_slug,
        organization__status=Organization.Status.ACTIVE,
        slug=job_slug,
        status=Job.Status.OPEN,
        deleted_at__isnull=True,
    )
    if request.method == "POST":
        form = PublicApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                application, _ = create_public_application(
                    job=job,
                    upload=form.cleaned_data["resume"],
                    **{key: form.cleaned_data[key] for key in ("first_name", "last_name", "email", "phone", "consent")},
                )
                return _render(
                    request,
                    "careers/application_success.html",
                    {"organization": job.organization, "job": job, "reference": application.id},
                    status=202,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
    else:
        form = PublicApplicationForm()
    return _render(
        request,
        "careers/apply.html",
        {"organization": job.organization, "job": job, "form": form},
    )


@login_required
def dashboard(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    applications = scope_applications(membership)
    jobs = scope_jobs(membership, Job.objects.filter(deleted_at__isnull=True))
    upcoming = scope_interviews(
        membership,
        Interview.objects.filter(
            start_at__gte=timezone.now(),
            status=Interview.Status.SCHEDULED,
        ).select_related("application__candidate", "application__job"),
    )
    activity = AuditLog.objects.filter(organization=organization).select_related("actor")
    if not has_permission(membership, "audit.view"):
        activity = activity.filter(actor=request.user)

    # Reuse the reporting layer so the dashboard and the reports page can never disagree.
    report = report_services.build_report(membership, "90")
    tasks = scope_tasks(membership, Task.objects.filter(status__in=OPEN_TASK_STATUSES))
    context = {
        "organization": organization,
        "membership": membership,
        "active_nav": "dashboard",
        "open_jobs": jobs.filter(status=Job.Status.OPEN).count(),
        "active_candidates": applications.filter(status=Application.Status.ACTIVE).values("candidate_id").distinct().count(),
        "weekly_interviews": upcoming.filter(start_at__lte=timezone.now() + timedelta(days=7)).count(),
        "parsing_failures": scope_resumes(membership).filter(
            parser_status=Resume.ParserStatus.FAILED
        ).count(),
        "funnel": report["funnel"],
        "chart_data": {"funnel": {
            "labels": [row["stage"] for row in report["funnel"]],
            "values": [row["count"] for row in report["funnel"]],
        }},
        "time_to_hire": report["time_to_hire"],
        "aging": report["aging"][:5],
        "open_task_count": tasks.count(),
        "overdue_task_count": tasks.filter(due_at__lt=timezone.now()).count(),
        "my_tasks": tasks.filter(assignee=request.user).select_related("job", "application__candidate")[:5],
        "upcoming": upcoming[:6],
        "activity": activity[:8],
        "can_manage_jobs": has_permission(membership, "jobs.manage"),
    }
    return _render(request, "dashboard/index.html", context)


def _scoped_jobs(organization, membership):
    return scope_jobs(membership, Job.objects.filter(deleted_at__isnull=True))


@login_required
def jobs_list(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    visible_application_ids = scope_applications(membership).values("id")
    jobs = (
        _scoped_jobs(organization, membership)
        .select_related("department", "location", "recruiter")
        .annotate(
            applicant_count=Count(
                "applications",
                filter=Q(applications__id__in=visible_application_ids),
                distinct=True,
            )
        )
        # annotate() adds a GROUP BY, which makes Django consider the queryset unordered.
        # Order explicitly so pagination stays stable.
        .order_by("-created_at")
    )
    status_filter = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if status_filter:
        jobs = jobs.filter(status=status_filter)
    if query:
        jobs = jobs.filter(Q(title__icontains=query) | Q(internal_code__icontains=query))
    page, querystring = paginate(request, jobs, per_page=20)
    return _render(
        request,
        "jobs/list.html",
        {
            "organization": organization,
            "membership": membership,
            "page": page,
            "querystring": querystring,
            "active_nav": "jobs",
            "query": query,
            "status_filter": status_filter,
            "can_manage_jobs": has_permission(membership, "jobs.manage"),
        },
    )


@login_required
def job_create(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "jobs.manage")
    if request.method == "POST":
        form = JobForm(request.POST, organization=organization)
        if form.is_valid():
            job = create_job(
                organization=organization,
                actor=request.user,
                membership=membership,
                data={name: form.cleaned_data[name] for name in JobForm.Meta.fields},
            )
            messages.success(request, "Job draft created.")
            return redirect("job-detail", organization_slug=organization.slug, job_id=job.id)
    else:
        form = JobForm(organization=organization)
    return _render(
        request,
        "jobs/form.html",
        {
            "organization": organization,
            "membership": membership,
            "form": form,
            "active_nav": "jobs",
            "form_title": "Create a job",
            "form_eyebrow": "New opening",
            "submit_label": "Create draft",
        },
    )


@login_required
def job_edit(request, organization_slug, job_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "jobs.manage")
    job = get_object_or_404(
        _scoped_jobs(organization, membership).select_related("department", "location"),
        id=job_id,
        deleted_at__isnull=True,
    )
    if request.method == "POST":
        form = JobForm(request.POST, instance=job, organization=organization)
        if form.is_valid():
            job = update_job(
                job_id=job.id,
                organization=organization,
                actor=request.user,
                membership=membership,
                data={name: form.cleaned_data[name] for name in JobForm.Meta.fields},
                request=request,
            )
            messages.success(request, "Job updated.")
            return redirect("job-detail", organization_slug=organization.slug, job_id=job.id)
    else:
        form = JobForm(instance=job, organization=organization)
    return _render(
        request,
        "jobs/form.html",
        {
            "organization": organization,
            "membership": membership,
            "form": form,
            "job": job,
            "active_nav": "jobs",
            "form_title": "Edit job",
            "form_eyebrow": job.title,
            "submit_label": "Save changes",
        },
    )


@login_required
def job_detail(request, organization_slug, job_id):
    organization, membership = _organization_context(request, organization_slug)
    job = get_object_or_404(
        _scoped_jobs(organization, membership).select_related("department", "location", "recruiter", "hiring_manager"),
        id=job_id,
    )
    return _render(
        request,
        "jobs/detail.html",
        {
            "organization": organization,
            "membership": membership,
            "job": job,
            "stages": job.stages.all(),
            "applications": scope_applications(
                membership,
                job.applications.select_related("candidate", "current_stage"),
            )[:12],
            "can_publish_jobs": has_permission(membership, "jobs.publish"),
            "can_manage_jobs": has_permission(membership, "jobs.manage"),
            "can_manage_interviews": has_permission(membership, "interviews.manage"),
            "active_nav": "jobs",
        },
    )


@login_required
@require_POST
def job_transition(request, organization_slug, job_id):
    organization, membership = _organization_context(request, organization_slug)
    form = JobTransitionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "That job status change could not be applied.")
        return redirect("job-detail", organization_slug=organization.slug, job_id=job_id)
    try:
        transition_job(
            job_id=job_id,
            organization=organization,
            actor=request.user,
            membership=membership,
            target_status=form.cleaned_data["target_status"],
            expected_version=form.cleaned_data["expected_version"],
            request=request,
        )
        messages.success(request, "Job status updated.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("job-detail", organization_slug=organization.slug, job_id=job_id)


@login_required
def candidates_list(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    visible_applications = scope_applications(
        membership,
        Application.objects.select_related("job"),
    )
    candidates = scope_candidates(
        membership,
        Candidate.objects.filter(deleted_at__isnull=True).prefetch_related(
            "candidate_skills__skill",
            Prefetch(
                "applications",
                queryset=visible_applications,
                to_attr="visible_applications",
            ),
        ),
    )
    query = request.GET.get("q", "").strip()
    if query:
        candidates = candidates.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(current_title__icontains=query)
            | Q(candidate_skills__skill__name__icontains=query)
        ).distinct()
    page, querystring = paginate(request, candidates, per_page=20)
    return _render(
        request,
        "candidates/list.html",
        {
            "organization": organization,
            "membership": membership,
            "page": page,
            "querystring": querystring,
            "active_nav": "candidates",
            "query": query,
            "can_manage_candidates": has_permission(membership, "candidates.manage"),
        },
    )


@login_required
def candidate_detail(request, organization_slug, candidate_id):
    organization, membership = _organization_context(request, organization_slug)
    candidate = get_object_or_404(
        scope_candidates(
            membership,
            Candidate.objects.prefetch_related(
                "candidate_skills__skill",
                "resumes__parse_results",
                "applications__job",
                "applications__current_stage",
            ),
        ),
        id=candidate_id,
        deleted_at__isnull=True,
    )
    can_view_contact = can_view_candidate_contact(membership)
    can_manage_candidates = has_permission(membership, "candidates.manage")
    visible_candidates = scope_candidates(membership).values("id")
    return _render(
        request,
        "candidates/detail.html",
        {
            "organization": organization,
            "membership": membership,
            "candidate": candidate,
            "can_view_contact": can_view_contact,
            "applications": scope_applications(
                membership,
                candidate.applications.select_related("job", "current_stage"),
            ),
            "duplicate_suggestions": candidate.duplicate_suggestions.filter(
                status=DuplicateSuggestion.Status.PENDING,
                possible_duplicate_id__in=visible_candidates,
            ).select_related("possible_duplicate") if can_manage_candidates else [],
            "can_manage_candidates": can_manage_candidates,
            "can_manage_interviews": has_permission(membership, "interviews.manage"),
            "active_nav": "candidates",
        },
    )


@login_required
def candidate_create(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "candidates.manage")
    if request.method == "POST":
        form = CandidateForm(request.POST)
        if form.is_valid():
            candidate = create_candidate(
                organization=organization,
                actor=request.user,
                membership=membership,
                data=form.cleaned_data,
            )
            messages.success(request, "Candidate created.")
            return redirect(
                "candidate-detail", organization_slug=organization.slug, candidate_id=candidate.id
            )
    else:
        form = CandidateForm()
    return _render(
        request,
        "candidates/form.html",
        {
            "organization": organization,
            "membership": membership,
            "form": form,
            "active_nav": "candidates",
            "form_title": "Add a candidate",
            "form_eyebrow": "Talent database",
            "submit_label": "Create candidate",
        },
    )


@login_required
def candidate_edit(request, organization_slug, candidate_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "candidates.manage")
    candidate = get_object_or_404(
        scope_candidates(membership),
        id=candidate_id,
        deleted_at__isnull=True,
    )
    if request.method == "POST":
        form = CandidateForm(request.POST, instance=candidate)
        if form.is_valid():
            candidate = update_candidate(
                candidate=candidate,
                actor=request.user,
                membership=membership,
                data=form.cleaned_data,
            )
            messages.success(request, "Candidate updated.")
            return redirect(
                "candidate-detail", organization_slug=organization.slug, candidate_id=candidate.id
            )
    else:
        form = CandidateForm(instance=candidate)
    return _render(
        request,
        "candidates/form.html",
        {
            "organization": organization,
            "membership": membership,
            "form": form,
            "candidate": candidate,
            "active_nav": "candidates",
            "form_title": "Edit candidate",
            "form_eyebrow": str(candidate),
            "submit_label": "Save changes",
        },
    )


@login_required
def resume_download(request, organization_slug, resume_id):
    organization, membership = _organization_context(request, organization_slug)
    resume = get_object_or_404(scope_resumes(membership), id=resume_id)
    record_audit(
        organization=organization, actor=request.user, action="resume.downloaded", resource=resume, request=request
    )
    return FileResponse(resume.file.open("rb"), as_attachment=True, filename=resume.original_name)


@login_required
@require_POST
def duplicate_merge(request, organization_slug, suggestion_id):
    organization, membership = _organization_context(request, organization_slug)
    try:
        master = merge_candidates(
            suggestion_id=suggestion_id,
            organization=organization,
            actor=request.user,
            membership=membership,
        )
        messages.success(request, "Candidate records merged.")
        return redirect("candidate-detail", organization_slug=organization.slug, candidate_id=master.id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("candidates", organization_slug=organization.slug)


@login_required
def pipeline(request, organization_slug, job_id=None):
    organization, membership = _organization_context(request, organization_slug)
    jobs = _scoped_jobs(organization, membership).filter(status__in=[Job.Status.OPEN, Job.Status.PAUSED])
    job = get_object_or_404(jobs, id=job_id) if job_id else jobs.first()
    stages = []
    if job:
        for stage in job.stages.filter(active=True):
            stage.cards = scope_applications(
                membership,
                stage.applications.filter(status=Application.Status.ACTIVE).select_related(
                    "candidate", "assigned_recruiter", "match_score"
                ),
            )
            stages.append(stage)
    can_move = has_permission(membership, "applications.move")
    can_reject = has_permission(membership, "applications.reject")
    return _render(
        request,
        "pipeline/index.html",
        {
            "organization": organization,
            "membership": membership,
            "jobs": jobs,
            "job": job,
            "stages": stages,
            "active_nav": "pipeline",
            "can_move": can_move,
            "can_reject": can_reject,
            "rejection_reasons": RejectionReason.objects.filter(
                organization=organization,
                active=True,
            ) if can_reject else [],
        },
    )


@login_required
@require_POST
def move_stage_htmx(request, organization_slug, application_id):
    organization, membership = _organization_context(request, organization_slug)
    form = MoveStageForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Invalid move request.", status=400)
    try:
        application = move_application_stage(
            application_id=application_id,
            organization=organization,
            actor=request.user,
            membership=membership,
            request=request,
            **form.cleaned_data,
        )
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("pipeline-job", args=[organization.slug, application.job_id])
        return response
    except ValidationError as exc:
        return HttpResponse("; ".join(exc.messages), status=409)


@login_required
@require_POST
def reject_htmx(request, organization_slug, application_id):
    organization, membership = _organization_context(request, organization_slug)
    form = RejectApplicationForm(request.POST, organization=organization)
    if form.is_valid():
        try:
            application = reject_application(
                application_id=application_id,
                reason=form.cleaned_data["reason"],
                note=form.cleaned_data["note"],
                organization=organization,
                actor=request.user,
                membership=membership,
                request=request,
            )
            messages.success(request, "Application rejected.")
            return redirect("pipeline-job", organization_slug=organization.slug, job_id=application.job_id)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return redirect("pipeline", organization_slug=organization.slug)


@login_required
def interviews_list(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    interviews = scope_interviews(
        membership,
        Interview.objects.select_related(
            "application__candidate", "application__job"
        ).prefetch_related("participants__user", "scorecard_submissions"),
    )
    status_filter = request.GET.get("status", "")
    if status_filter:
        interviews = interviews.filter(status=status_filter)
    page, querystring = paginate(request, interviews, per_page=12)
    return _render(
        request,
        "interviews/list.html",
        {
            "organization": organization,
            "membership": membership,
            "page": page,
            "querystring": querystring,
            "status_filter": status_filter,
            "active_nav": "interviews",
            "can_manage_interviews": has_permission(membership, "interviews.manage"),
        },
    )


@login_required
def interview_create(request, organization_slug, application_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "interviews.manage")
    application = get_object_or_404(
        scope_applications(membership, Application.objects.select_related("job", "candidate")),
        id=application_id,
    )
    if request.method == "POST":
        form = InterviewForm(request.POST, application=application)
        if form.is_valid():
            participants = list(form.cleaned_data.pop("participants"))
            interview = schedule_interview(
                application=application,
                actor=request.user,
                membership=membership,
                data=form.cleaned_data,
                participants=participants,
                request=request,
            )
            messages.success(request, "Interview scheduled and invitations queued.")
            return redirect("scorecard", organization_slug=organization.slug, interview_id=interview.id)
    else:
        form = InterviewForm(application=application)
    return _render(
        request,
        "interviews/form.html",
        {"organization": organization, "membership": membership, "application": application, "form": form, "active_nav": "interviews"},
    )


@login_required
def scorecard(request, organization_slug, interview_id):
    organization, membership = _organization_context(request, organization_slug)
    interview = get_object_or_404(
        scope_interviews(
            membership,
            Interview.objects.select_related("application__candidate", "application__job"),
        ),
        id=interview_id,
    )
    is_participant = interview.participants.filter(user=request.user).exists()
    if not is_participant and membership.role not in {
        OrganizationMembership.Role.ORG_ADMIN,
        OrganizationMembership.Role.HR_ADMIN,
    }:
        raise Http404
    can_submit_scorecard = is_participant and has_permission(membership, "scorecards.submit")
    existing = ScorecardSubmission.objects.filter(interview=interview, submitted_by=request.user).prefetch_related("answers").first()
    if request.method == "POST":
        if not can_submit_scorecard:
            raise Http404
        answers = {
            row["id"]: {
                "rating": request.POST.get(f"rating_{row['id']}", ""),
                "evidence": request.POST.get(f"evidence_{row['id']}", ""),
            }
            for row in interview.template_snapshot.get("criteria", [])
            if request.POST.get(f"rating_{row['id']}")
        }
        try:
            save_scorecard(
                interview=interview,
                actor=request.user,
                recommendation=request.POST.get("recommendation", ""),
                summary=request.POST.get("summary", ""),
                conflict=request.POST.get("conflict_of_interest") == "on",
                answers=answers,
                submit=request.POST.get("action") == "submit",
            )
            messages.success(request, "Scorecard submitted and locked." if request.POST.get("action") == "submit" else "Draft saved.")
            return redirect("scorecard", organization_slug=organization.slug, interview_id=interview.id)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    answer_map = {str(row.criterion_id): row for row in existing.answers.all()} if existing else {}
    return _render(
        request,
        "interviews/scorecard.html",
        {
            "organization": organization,
            "membership": membership,
            "interview": interview,
            "submission": existing,
            "answer_map": answer_map,
            "can_submit_scorecard": can_submit_scorecard,
            "can_reopen_scorecard": has_permission(membership, "scorecards.reopen"),
            "active_nav": "interviews",
        },
    )


@login_required
@require_POST
def interview_cancel(request, organization_slug, interview_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "interviews.manage")
    _, changed = set_interview_status(
        interview_id=interview_id,
        organization=organization,
        actor=request.user,
        membership=membership,
        status=Interview.Status.CANCELLED,
        request=request,
    )
    if changed:
        messages.success(request, "Interview cancelled.")
    else:
        messages.info(request, "This interview was already cancelled.")
    return redirect("interviews", organization_slug=organization.slug)


@login_required
@require_POST
def interview_complete(request, organization_slug, interview_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "interviews.manage")
    _, changed = set_interview_status(
        interview_id=interview_id,
        organization=organization,
        actor=request.user,
        membership=membership,
        status=Interview.Status.COMPLETED,
        request=request,
    )
    if changed:
        messages.success(request, "Interview marked as completed.")
    else:
        messages.info(request, "This interview was already completed.")
    return redirect("interviews", organization_slug=organization.slug)


@login_required
@require_POST
def scorecard_reopen(request, organization_slug, interview_id, submission_id):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "scorecards.reopen")
    interview = get_object_or_404(
        scope_interviews(membership, Interview.objects.all()), id=interview_id
    )
    try:
        reopen_scorecard(
            submission_id=submission_id,
            organization=organization,
            actor=request.user,
            membership=membership,
            reason=request.POST.get("reason", ""),
            interview_id=interview.id,
        )
        messages.success(request, "Scorecard reopened. The interviewer can edit it again.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("scorecard", organization_slug=organization.slug, interview_id=interview.id)


@login_required
def profile(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    profile_form = ProfileForm(initial={"full_name": request.user.full_name})
    password_form = PasswordChangeForm(request.user)
    if request.method == "POST":
        action = request.POST.get("action", "profile")
        if action == "password":
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, request.user)
                record_audit(
                    organization=organization,
                    actor=request.user,
                    action="account.password_changed",
                    resource=request.user,
                    request=request,
                )
                messages.success(request, "Password updated.")
                return redirect("profile", organization_slug=organization.slug)
        else:
            profile_form = ProfileForm(request.POST)
            if profile_form.is_valid():
                request.user.full_name = profile_form.cleaned_data["full_name"]
                request.user.save(update_fields=["full_name"])
                record_audit(
                    organization=organization,
                    actor=request.user,
                    action="account.profile_updated",
                    resource=request.user,
                    request=request,
                )
                messages.success(request, "Profile updated.")
                return redirect("profile", organization_slug=organization.slug)
    return _render(
        request,
        "settings/profile.html",
        {
            "organization": organization,
            "membership": membership,
            "active_nav": "profile",
            "profile_form": profile_form,
            "password_form": password_form,
            "memberships": request.user.memberships.select_related("organization"),
            "mfa_confirmed": request.user.totpdevice_set.filter(confirmed=True).exists(),
        },
    )


@login_required
def organization_settings(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "members.invite")
    if request.method == "POST":
        form = InvitationForm(request.POST)
        if form.is_valid():
            invitation, token = Invitation.issue(
                organization=organization,
                email=form.cleaned_data["email"],
                role=form.cleaned_data["role"],
                invited_by=request.user,
            )
            link = request.build_absolute_uri(reverse("accept-invitation", args=[token]))
            send_mail(
                f"Invitation to {organization.name}",
                f"Accept your invitation within 48 hours: {link}",
                None,
                [invitation.email],
            )
            record_audit(organization=organization, actor=request.user, action="member.invited", resource=invitation)
            messages.success(request, "Invitation sent.")
            return redirect("organization-settings", organization_slug=organization.slug)
    else:
        form = InvitationForm()
    return _render(
        request,
        "settings/organization.html",
        {
            "organization": organization,
            "membership": membership,
            "memberships": organization.memberships.select_related("user"),
            "invitations": organization.invitations.filter(accepted_at__isnull=True),
            "form": form,
            "active_nav": "settings",
        },
    )


@login_required
def audit_log(request, organization_slug):
    organization, membership = _organization_context(request, organization_slug)
    require_permission(membership, "audit.view")
    events = AuditLog.objects.filter(organization=organization).select_related("actor")
    query = request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "")
    if query:
        events = events.filter(
            Q(action__icontains=query)
            | Q(resource_type__icontains=query)
            | Q(actor__full_name__icontains=query)
        )
    if action_filter:
        events = events.filter(action=action_filter)
    page, querystring = paginate(request, events, per_page=25)
    return _render(
        request,
        "audit/list.html",
        {
            "organization": organization,
            "membership": membership,
            "page": page,
            "querystring": querystring,
            "active_nav": "audit",
            "query": query,
            "action_filter": action_filter,
            "actions": list(
                AuditLog.objects.filter(organization=organization)
                .order_by("action")
                .values_list("action", flat=True)
                .distinct()
            ),
        },
    )
