from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Organization, User
from apps.accounts.policies import (
    get_membership,
    has_permission,
    require_permission,
    scope_applications,
    scope_candidates,
    scope_interviews,
    scope_jobs,
    scope_resumes,
    scope_tasks,
)
from apps.applications.models import Application, IdempotencyRecord
from apps.applications.services import move_application_stage, reject_application
from apps.candidates.models import Candidate, Resume
from apps.candidates.services import create_candidate, create_public_application, update_candidate
from apps.interviews.models import Interview
from apps.interviews.services import save_scorecard, schedule_interview, set_interview_status
from apps.jobs.models import Job, ScorecardTemplate
from apps.jobs.services import create_job, transition_job, update_job
from apps.reports import services as report_services
from apps.tasks.models import Task
from apps.tasks.services import create_task, update_task

from .responses import EnvelopedMixin, StandardPagination, envelope
from .serializers import (
    ApplicationSerializer,
    CandidateSerializer,
    CandidateWriteSerializer,
    InterviewSerializer,
    InterviewWriteSerializer,
    JobSerializer,
    JobTransitionSerializer,
    JobWriteSerializer,
    MoveStageSerializer,
    PublicApplicationSerializer,
    PublicJobSerializer,
    RejectApplicationSerializer,
    ResumeSerializer,
    ScorecardSubmitSerializer,
    TaskSerializer,
    TaskWriteSerializer,
)


class OrganizationAPIMixin:
    """Resolve the tenant and the caller's membership once per request."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.organization = get_object_or_404(Organization, slug=kwargs["organization_slug"])
        self.membership = get_membership(request.user, self.organization)


class OrgListCreateView(EnvelopedMixin, OrganizationAPIMixin, generics.ListCreateAPIView):
    pagination_class = StandardPagination


class OrgListRetrieveView(EnvelopedMixin, OrganizationAPIMixin, generics.ListAPIView):
    pagination_class = StandardPagination


class OrgRetrieveUpdateView(EnvelopedMixin, OrganizationAPIMixin, generics.RetrieveUpdateAPIView):
    pass


class OrgActionView(EnvelopedMixin, OrganizationAPIMixin, APIView):
    """Base for hand-written write endpoints that return the affected resource."""


# --------------------------------------------------------------------------- public


class PublicJobsView(EnvelopedMixin, generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicJobSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return Job.objects.filter(
            organization__slug=self.kwargs["organization_slug"],
            organization__status=Organization.Status.ACTIVE,
            status=Job.Status.OPEN,
            deleted_at__isnull=True,
        ).select_related("department", "location")


class PublicJobDetailView(EnvelopedMixin, generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicJobSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "job_slug"

    def get_queryset(self):
        return Job.objects.filter(
            organization__slug=self.kwargs["organization_slug"],
            organization__status=Organization.Status.ACTIVE,
            status=Job.Status.OPEN,
            deleted_at__isnull=True,
        ).select_related("department", "location")


class PublicApplyView(EnvelopedMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_apply"

    @extend_schema(request=PublicApplicationSerializer)
    def post(self, request, organization_slug, job_slug):
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not idempotency_key:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "idempotency_required",
                        "message": "Idempotency-Key is required.",
                        "fields": {},
                        "request_id": getattr(request, "request_id", ""),
                    },
                },
                status=400,
            )
        serializer = PublicApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = get_object_or_404(
            Job.objects.select_related("organization"),
            organization__slug=organization_slug,
            organization__status=Organization.Status.ACTIVE,
            slug=job_slug,
            status=Job.Status.OPEN,
            deleted_at__isnull=True,
        )
        existing = IdempotencyRecord.objects.filter(
            organization=job.organization,
            action="public.apply",
            key=idempotency_key,
        ).first()
        if existing:
            return Response(
                {"success": True, "message": "Application already received.", "data": existing.response},
                status=status.HTTP_202_ACCEPTED,
            )
        payload = dict(serializer.validated_data)
        application, _ = create_public_application(job=job, upload=payload.pop("resume"), **payload)
        response_data = {"status": "processing", "application_reference": str(application.id)}
        IdempotencyRecord.objects.create(
            organization=job.organization,
            key=idempotency_key,
            action="public.apply",
            resource_type="applications.application",
            resource_id=str(application.id),
            response=response_data,
        )
        return Response(
            {"success": True, "message": "Application received.", "data": response_data},
            status=status.HTTP_202_ACCEPTED,
        )


# --------------------------------------------------------------------------- jobs


class JobsView(OrgListCreateView):
    """List the jobs this role can see, or create a new draft."""

    def get_serializer_class(self):
        return JobWriteSerializer if self.request.method == "POST" else JobSerializer

    def get_queryset(self):
        queryset = scope_jobs(
            self.membership,
            Job.objects.filter(deleted_at__isnull=True).select_related("department", "location"),
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        search = self.request.query_params.get("q")
        if search:
            queryset = queryset.filter(title__icontains=search)
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = JobWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = create_job(
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
        )
        return envelope(JobSerializer(job).data, message="Job draft created.", status_code=201)


class JobDetailView(OrgRetrieveUpdateView):
    serializer_class = JobSerializer
    lookup_url_kwarg = "job_id"

    def get_queryset(self):
        return scope_jobs(
            self.membership, Job.objects.filter(deleted_at__isnull=True)
        ).select_related("department", "location")

    def patch(self, request, *args, **kwargs):
        job = self.get_object()
        serializer = JobWriteSerializer(job, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        job = update_job(
            job_id=job.id,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
            request=request,
        )
        return envelope(JobSerializer(job).data, message="Job updated.")


class JobTransitionView(OrgActionView):
    @extend_schema(request=JobTransitionSerializer)
    def post(self, request, organization_slug, job_id):
        serializer = JobTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = transition_job(
            job_id=job_id,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            target_status=serializer.validated_data["target_status"],
            expected_version=serializer.validated_data.get("expected_version"),
            request=request,
        )
        return envelope(JobSerializer(job).data, message=f"Job is now {job.get_status_display()}.")


# --------------------------------------------------------------------------- candidates


class CandidatesView(OrgListCreateView):
    def get_serializer_class(self):
        return CandidateWriteSerializer if self.request.method == "POST" else CandidateSerializer

    def get_queryset(self):
        queryset = scope_candidates(
            self.membership,
            Candidate.objects.filter(deleted_at__isnull=True).prefetch_related(
                "candidate_skills__skill"
            ),
        )
        search = self.request.query_params.get("q")
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(current_title__icontains=search)
            )
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = CandidateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate = create_candidate(
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
        )
        return envelope(CandidateSerializer(candidate).data, message="Candidate created.", status_code=201)


class CandidateDetailView(OrgRetrieveUpdateView):
    serializer_class = CandidateSerializer

    def get_queryset(self):
        return scope_candidates(self.membership, Candidate.objects.filter(deleted_at__isnull=True))

    def patch(self, request, *args, **kwargs):
        candidate = self.get_object()
        serializer = CandidateWriteSerializer(candidate, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        candidate = update_candidate(
            candidate=candidate,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
        )
        return envelope(CandidateSerializer(candidate).data, message="Candidate updated.")


class ResumesView(OrgListRetrieveView):
    serializer_class = ResumeSerializer

    def get_queryset(self):
        queryset = scope_resumes(self.membership, Resume.objects.select_related("candidate"))
        status_filter = self.request.query_params.get("parser_status")
        if status_filter:
            queryset = queryset.filter(parser_status=status_filter)
        return queryset.order_by("-created_at")


# --------------------------------------------------------------------------- applications


class ApplicationsView(OrgListRetrieveView):
    serializer_class = ApplicationSerializer

    def get_queryset(self):
        queryset = scope_applications(
            self.membership,
            Application.objects.select_related("job", "candidate", "current_stage"),
        )
        job_id = self.request.query_params.get("job")
        if job_id:
            queryset = queryset.filter(job_id=job_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        stage_type = self.request.query_params.get("stage_type")
        if stage_type:
            queryset = queryset.filter(current_stage__stage_type=stage_type)
        return queryset.order_by("-applied_at")


class MoveApplicationView(OrgActionView):
    @extend_schema(request=MoveStageSerializer)
    def post(self, request, organization_slug, application_id):
        serializer = MoveStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = move_application_stage(
            application_id=application_id,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            request=request,
            **serializer.validated_data,
        )
        return envelope(ApplicationSerializer(application).data, message="Application moved.")


class RejectApplicationView(OrgActionView):
    @extend_schema(request=RejectApplicationSerializer)
    def post(self, request, organization_slug, application_id):
        serializer = RejectApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        if reason.organization_id != self.organization.id:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "invalid",
                        "message": "Unknown rejection reason.",
                        "fields": {"reason": ["Unknown rejection reason."]},
                        "request_id": getattr(request, "request_id", ""),
                    },
                },
                status=400,
            )
        application = reject_application(
            application_id=application_id,
            reason=reason,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        return envelope(ApplicationSerializer(application).data, message="Application rejected.")


# --------------------------------------------------------------------------- interviews


class InterviewsView(OrgListCreateView):
    def get_serializer_class(self):
        return InterviewWriteSerializer if self.request.method == "POST" else InterviewSerializer

    def get_queryset(self):
        queryset = scope_interviews(
            self.membership,
            Interview.objects.select_related("application__candidate").prefetch_related("participants"),
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        application_id = self.request.query_params.get("application")
        if application_id:
            queryset = queryset.filter(application_id=application_id)
        return queryset.order_by("start_at")

    def create(self, request, *args, **kwargs):
        serializer = InterviewWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        participant_ids = data.pop("participants")
        application = get_object_or_404(
            scope_applications(self.membership), id=data.pop("application")
        )
        template = get_object_or_404(
            ScorecardTemplate,
            id=data.pop("scorecard_template"),
            job=application.job,
            active=True,
        )
        # `schedule_interview` builds `InterviewParticipant(user=...)` and records
        # `str(user.id)` in the audit, so it needs User instances -- not the bare ids that
        # `values_list("user_id")` produced, which raised a ValueError deep in the ORM and
        # turned every API scheduling request into a 500.
        participants = list(
            User.objects.filter(
                memberships__organization=self.organization,
                memberships__active=True,
                id__in=participant_ids,
            ).distinct()
        )
        if len(participants) != len(set(participant_ids)):
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "invalid",
                        "message": "Every participant must be an active member of this workspace.",
                        "fields": {"participants": ["Unknown participant."]},
                        "request_id": getattr(request, "request_id", ""),
                    },
                },
                status=400,
            )
        interview = schedule_interview(
            application=application,
            actor=request.user,
            membership=self.membership,
            data={**data, "scorecard_template": template},
            participants=list(participants),
            request=request,
        )
        return envelope(
            InterviewSerializer(interview).data, message="Interview scheduled.", status_code=201
        )


class InterviewDetailView(EnvelopedMixin, OrganizationAPIMixin, generics.RetrieveAPIView):
    serializer_class = InterviewSerializer

    def get_queryset(self):
        return scope_interviews(self.membership, Interview.objects.all())


class InterviewStatusView(OrgActionView):
    """Shared handler for cancelling and completing an interview."""

    target_status = None

    def post(self, request, organization_slug, interview_id):
        interview, changed = set_interview_status(
            interview_id=interview_id,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            status=self.target_status,
            request=request,
        )
        message = f"Interview {interview.get_status_display().lower()}."
        if not changed:
            message = "No change — the interview was already in that state."
        return envelope(InterviewSerializer(interview).data, message=message)


class InterviewCancelView(InterviewStatusView):
    target_status = Interview.Status.CANCELLED


class InterviewCompleteView(InterviewStatusView):
    target_status = Interview.Status.COMPLETED


class ScorecardSubmitView(OrgActionView):
    @extend_schema(request=ScorecardSubmitSerializer)
    def post(self, request, organization_slug, interview_id):
        interview = get_object_or_404(
            scope_interviews(
                self.membership,
                Interview.objects.select_related("application__organization"),
            ),
            id=interview_id,
        )
        serializer = ScorecardSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = save_scorecard(
            interview=interview,
            actor=request.user,
            recommendation=serializer.validated_data["recommendation"],
            summary=serializer.validated_data.get("summary", ""),
            conflict=serializer.validated_data.get("conflict_of_interest", False),
            answers=serializer.validated_data["answers"],
            submit=True,
        )
        return envelope(
            {"id": str(submission.id), "status": submission.status}, message="Scorecard submitted."
        )


# --------------------------------------------------------------------------- tasks


class TasksView(OrgListCreateView):
    def get_serializer_class(self):
        return TaskWriteSerializer if self.request.method == "POST" else TaskSerializer

    def get_queryset(self):
        queryset = scope_tasks(
            self.membership,
            Task.objects.select_related("assignee", "job", "application__candidate"),
        )
        status_filter = self.request.query_params.get("status")
        if status_filter == "open":
            queryset = queryset.filter(status__in=[Task.Status.OPEN, Task.Status.IN_PROGRESS])
        elif status_filter:
            queryset = queryset.filter(status=status_filter)
        assignee = self.request.query_params.get("assignee")
        if assignee == "me":
            queryset = queryset.filter(assignee=self.request.user)
        elif assignee:
            queryset = queryset.filter(assignee_id=assignee)
        return queryset.order_by("status", "due_at", "-created_at")

    def create(self, request, *args, **kwargs):
        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = create_task(
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
        )
        return envelope(TaskSerializer(task).data, message="Task created.", status_code=201)


class TaskDetailView(OrgRetrieveUpdateView):
    serializer_class = TaskSerializer

    def get_queryset(self):
        return scope_tasks(
            self.membership,
            Task.objects.select_related("assignee", "job", "application__candidate"),
        )

    def patch(self, request, *args, **kwargs):
        task = self.get_object()
        serializer = TaskWriteSerializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        task = update_task(
            task_id=task.id,
            organization=self.organization,
            actor=request.user,
            membership=self.membership,
            data=serializer.validated_data,
        )
        return envelope(TaskSerializer(task).data, message="Task updated.")


# --------------------------------------------------------------------------- analytics


class ReportsView(EnvelopedMixin, OrganizationAPIMixin, APIView):
    def get(self, request, organization_slug):
        require_permission(self.membership, "reports.view")
        report = report_services.build_report(self.membership, request.query_params.get("range"))
        return envelope(_report_payload(report), message=report["range_label"])


class DashboardView(EnvelopedMixin, OrganizationAPIMixin, APIView):
    def get(self, request, organization_slug):
        report = report_services.build_report(self.membership, request.query_params.get("range"))
        tasks = scope_tasks(
            self.membership,
            Task.objects.filter(status__in=[Task.Status.OPEN, Task.Status.IN_PROGRESS]),
        )
        return envelope(
            {
                "summary": report["summary"],
                "funnel": report["funnel"],
                "time_to_hire": report["time_to_hire"],
                "aging": _aging_payload(report["aging"]),
                "tasks": {
                    "open": tasks.count(),
                    "overdue": tasks.filter(due_at__lt=timezone.now()).count(),
                    "mine": tasks.filter(assignee=request.user).count(),
                },
                "can_manage_jobs": has_permission(self.membership, "jobs.manage"),
            }
        )


def _aging_payload(rows) -> list[dict]:
    """Drop model instances so the payload stays JSON-serialisable for API clients."""
    return [
        {
            "application_id": str(row["application"].id),
            "candidate": str(row["application"].candidate),
            "job": row["application"].job.title,
            "stage": row["application"].current_stage.name,
            "days_in_stage": row["days_in_stage"],
            "sla_hours": row["sla_hours"],
            "overdue": row["overdue"],
        }
        for row in rows
    ]


def _report_payload(report: dict) -> dict:
    return {
        "range_token": report["range_token"],
        "range_label": report["range_label"],
        "summary": report["summary"],
        "funnel": report["funnel"],
        "time_in_stage": report["time_in_stage"],
        "time_to_hire": report["time_to_hire"],
        "source_quality": report["source_quality"],
        "interviewer_completion": report["interviewer_completion"],
        "aging": _aging_payload(report["aging"]),
    }
