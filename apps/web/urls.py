from django.urls import include, path

from apps.accounts.views import app_entry

from . import views

urlpatterns = [
    path("", views.landing, name="home"),
    path("healthz", views.health, name="health"),
    path("readyz", views.readiness, name="readiness"),
    path("careers/<slug:organization_slug>/", views.public_jobs, name="public-jobs"),
    path("careers/<slug:organization_slug>/jobs/<slug:job_slug>/", views.public_job_detail, name="public-job-detail"),
    path("careers/<slug:organization_slug>/jobs/<slug:job_slug>/apply/", views.public_apply, name="public-apply"),
    path("app/", app_entry, name="app-entry"),
    path("app/<slug:organization_slug>/", views.dashboard, name="dashboard"),
    path("app/<slug:organization_slug>/jobs/", views.jobs_list, name="jobs"),
    path("app/<slug:organization_slug>/jobs/new/", views.job_create, name="job-create"),
    path("app/<slug:organization_slug>/jobs/<uuid:job_id>/", views.job_detail, name="job-detail"),
    path("app/<slug:organization_slug>/jobs/<uuid:job_id>/edit/", views.job_edit, name="job-edit"),
    path("app/<slug:organization_slug>/jobs/<uuid:job_id>/transition/", views.job_transition, name="job-transition"),
    path("app/<slug:organization_slug>/candidates/", views.candidates_list, name="candidates"),
    path("app/<slug:organization_slug>/candidates/new/", views.candidate_create, name="candidate-create"),
    path("app/<slug:organization_slug>/candidates/<uuid:candidate_id>/", views.candidate_detail, name="candidate-detail"),
    path("app/<slug:organization_slug>/candidates/<uuid:candidate_id>/edit/", views.candidate_edit, name="candidate-edit"),
    path("app/<slug:organization_slug>/resumes/<uuid:resume_id>/download/", views.resume_download, name="resume-download"),
    path("app/<slug:organization_slug>/duplicates/<uuid:suggestion_id>/merge/", views.duplicate_merge, name="duplicate-merge"),
    path("app/<slug:organization_slug>/pipeline/", views.pipeline, name="pipeline"),
    path("app/<slug:organization_slug>/pipeline/<uuid:job_id>/", views.pipeline, name="pipeline-job"),
    path("app/<slug:organization_slug>/applications/<uuid:application_id>/move-stage/", views.move_stage_htmx, name="move-stage"),
    path("app/<slug:organization_slug>/applications/<uuid:application_id>/reject/", views.reject_htmx, name="reject-application"),
    path("app/<slug:organization_slug>/interviews/", views.interviews_list, name="interviews"),
    path("app/<slug:organization_slug>/applications/<uuid:application_id>/interviews/new/", views.interview_create, name="interview-create"),
    path("app/<slug:organization_slug>/interviews/<uuid:interview_id>/scorecard/", views.scorecard, name="scorecard"),
    path("app/<slug:organization_slug>/interviews/<uuid:interview_id>/cancel/", views.interview_cancel, name="interview-cancel"),
    path("app/<slug:organization_slug>/interviews/<uuid:interview_id>/complete/", views.interview_complete, name="interview-complete"),
    path(
        "app/<slug:organization_slug>/interviews/<uuid:interview_id>/scorecards/<uuid:submission_id>/reopen/",
        views.scorecard_reopen,
        name="scorecard-reopen",
    ),
    path("app/<slug:organization_slug>/settings/", views.organization_settings, name="organization-settings"),
    path("app/<slug:organization_slug>/profile/", views.profile, name="profile"),
    path("app/<slug:organization_slug>/audit/", views.audit_log, name="audit-log"),
    path("app/<slug:organization_slug>/", include("apps.tasks.urls")),
    path("app/<slug:organization_slug>/", include("apps.reports.urls")),
]
