from django.urls import path

from . import views

urlpatterns = [
    # Public careers API — no authentication, throttled on the write path.
    path("public/organizations/<slug:organization_slug>/jobs", views.PublicJobsView.as_view(), name="api-public-jobs"),
    path(
        "public/organizations/<slug:organization_slug>/jobs/<slug:job_slug>",
        views.PublicJobDetailView.as_view(),
        name="api-public-job",
    ),
    path(
        "public/organizations/<slug:organization_slug>/jobs/<slug:job_slug>/apply",
        views.PublicApplyView.as_view(),
        name="api-public-apply",
    ),
    # Jobs
    path("organizations/<slug:organization_slug>/jobs", views.JobsView.as_view(), name="api-jobs"),
    path(
        "organizations/<slug:organization_slug>/jobs/<uuid:job_id>",
        views.JobDetailView.as_view(),
        name="api-job",
    ),
    path(
        "organizations/<slug:organization_slug>/jobs/<uuid:job_id>/transition",
        views.JobTransitionView.as_view(),
        name="api-job-transition",
    ),
    # Candidates
    path("organizations/<slug:organization_slug>/candidates", views.CandidatesView.as_view(), name="api-candidates"),
    path(
        "organizations/<slug:organization_slug>/candidates/<uuid:pk>",
        views.CandidateDetailView.as_view(),
        name="api-candidate",
    ),
    path("organizations/<slug:organization_slug>/resumes", views.ResumesView.as_view(), name="api-resumes"),
    # Applications
    path(
        "organizations/<slug:organization_slug>/applications",
        views.ApplicationsView.as_view(),
        name="api-applications",
    ),
    path(
        "organizations/<slug:organization_slug>/applications/<uuid:application_id>/move-stage",
        views.MoveApplicationView.as_view(),
        name="api-move-application",
    ),
    path(
        "organizations/<slug:organization_slug>/applications/<uuid:application_id>/reject",
        views.RejectApplicationView.as_view(),
        name="api-reject-application",
    ),
    # Interviews
    path("organizations/<slug:organization_slug>/interviews", views.InterviewsView.as_view(), name="api-interviews"),
    path(
        "organizations/<slug:organization_slug>/interviews/<uuid:pk>",
        views.InterviewDetailView.as_view(),
        name="api-interview",
    ),
    path(
        "organizations/<slug:organization_slug>/interviews/<uuid:interview_id>/cancel",
        views.InterviewCancelView.as_view(),
        name="api-interview-cancel",
    ),
    path(
        "organizations/<slug:organization_slug>/interviews/<uuid:interview_id>/complete",
        views.InterviewCompleteView.as_view(),
        name="api-interview-complete",
    ),
    path(
        "organizations/<slug:organization_slug>/interviews/<uuid:interview_id>/scorecards/submit",
        views.ScorecardSubmitView.as_view(),
        name="api-submit-scorecard",
    ),
    # Tasks
    path("organizations/<slug:organization_slug>/tasks", views.TasksView.as_view(), name="api-tasks"),
    path(
        "organizations/<slug:organization_slug>/tasks/<uuid:pk>",
        views.TaskDetailView.as_view(),
        name="api-task",
    ),
    # Analytics
    path("organizations/<slug:organization_slug>/reports", views.ReportsView.as_view(), name="api-reports"),
    path("organizations/<slug:organization_slug>/dashboard", views.DashboardView.as_view(), name="api-dashboard"),
]
