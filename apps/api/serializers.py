from rest_framework import serializers

from apps.applications.models import Application, RejectionReason
from apps.candidates.models import Candidate, Resume
from apps.interviews.models import Interview, ScorecardSubmission
from apps.jobs.models import Job, PipelineStage
from apps.tasks.models import Task


class PublicJobSerializer(serializers.ModelSerializer):
    department = serializers.CharField(source="department.name")
    location = serializers.CharField(source="location.name")
    workplace_type = serializers.CharField(source="location.workplace_type")

    class Meta:
        model = Job
        fields = [
            "id",
            "title",
            "slug",
            "department",
            "location",
            "workplace_type",
            "employment_type",
            "description_html",
            "requirements_html",
            "published_at",
        ]


class PublicApplicationSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, allow_blank=True, required=False)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=40, allow_blank=True, required=False)
    resume = serializers.FileField()
    consent = serializers.BooleanField()


class JobSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    applicant_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Job
        fields = [
            "id",
            "title",
            "slug",
            "status",
            "department_name",
            "location_name",
            "employment_type",
            "headcount",
            "description_html",
            "requirements_html",
            "min_experience_months",
            "education_level",
            "applicant_count",
            "version",
            "created_at",
        ]


class JobWriteSerializer(serializers.ModelSerializer):
    """Create/update payload for a job. Publishing is a separate, permissioned transition."""

    class Meta:
        model = Job
        fields = [
            "title",
            "department",
            "location",
            "hiring_manager",
            "recruiter",
            "pipeline_template",
            "employment_type",
            "headcount",
            "description_html",
            "requirements_html",
            "min_experience_months",
            "education_level",
        ]


class JobTransitionSerializer(serializers.Serializer):
    target_status = serializers.ChoiceField(choices=Job.Status.choices)
    expected_version = serializers.IntegerField(min_value=1, required=False)


class CandidateSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="__str__", read_only=True)
    skills = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = [
            "id",
            "name",
            "city",
            "country_code",
            "current_title",
            "current_company",
            "total_experience_months",
            "education_level",
            "source_first",
            "skills",
            "created_at",
        ]

    def get_skills(self, obj):
        return [row.skill.name for row in obj.candidate_skills.select_related("skill")]


class CandidateWriteSerializer(serializers.ModelSerializer):
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=40, allow_blank=True, required=False)

    class Meta:
        model = Candidate
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "city",
            "country_code",
            "current_title",
            "current_company",
            "total_experience_months",
            "education_level",
            "summary",
            "source_first",
        ]

    def validate_email(self, value):
        return value.strip().casefold()

    def validate_country_code(self, value):
        return (value or "").strip().upper()


class ResumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resume
        fields = [
            "id",
            "candidate_id",
            "original_name",
            "mime_type",
            "size_bytes",
            "parser_status",
            "parser_version",
            "failure_reason",
            "created_at",
        ]


class PipelineStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineStage
        fields = ["id", "name", "stage_type", "position", "requires_interview", "requires_scorecard"]


class ApplicationSerializer(serializers.ModelSerializer):
    candidate = CandidateSerializer(read_only=True)
    job_title = serializers.CharField(source="job.title", read_only=True)
    stage = PipelineStageSerializer(source="current_stage", read_only=True)
    match_score = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = [
            "id",
            "job_id",
            "job_title",
            "candidate",
            "stage",
            "status",
            "source",
            "applied_at",
            "version",
            "match_score",
        ]

    def get_match_score(self, obj):
        try:
            score = obj.match_score
        except Exception:
            return None
        return {"total": score.total_score, "version": score.model_version, "explanation": score.explanation}


class MoveStageSerializer(serializers.Serializer):
    target_stage_id = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=255, allow_blank=True, required=False)


class RejectApplicationSerializer(serializers.Serializer):
    reason = serializers.PrimaryKeyRelatedField(queryset=RejectionReason.objects.all())
    note = serializers.CharField(max_length=255, allow_blank=True, required=False)


class InterviewSerializer(serializers.ModelSerializer):
    participant_ids = serializers.SerializerMethodField()
    candidate_name = serializers.SerializerMethodField()

    class Meta:
        model = Interview
        fields = [
            "id",
            "application_id",
            "candidate_name",
            "title",
            "interview_type",
            "start_at",
            "end_at",
            "timezone",
            "meeting_url",
            "status",
            "participant_ids",
        ]

    def get_participant_ids(self, obj):
        return [row.user_id for row in obj.participants.all()]

    def get_candidate_name(self, obj):
        return str(obj.application.candidate)


class InterviewWriteSerializer(serializers.Serializer):
    """Scheduling payload. The service resolves the scorecard snapshot from the template."""

    application = serializers.UUIDField()
    scorecard_template = serializers.UUIDField()
    title = serializers.CharField(max_length=160)
    interview_type = serializers.CharField(max_length=40, required=False, default="VIDEO")
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    timezone = serializers.CharField(max_length=64, required=False, default="UTC")
    location = serializers.CharField(max_length=255, allow_blank=True, required=False)
    meeting_url = serializers.URLField(allow_blank=True, required=False)
    participants = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)

    def validate(self, attrs):
        if attrs["end_at"] <= attrs["start_at"]:
            raise serializers.ValidationError("The interview must end after it starts.")
        return attrs


class ScorecardSubmitSerializer(serializers.Serializer):
    recommendation = serializers.ChoiceField(choices=ScorecardSubmission.Recommendation.choices)
    summary = serializers.CharField(allow_blank=True, required=False)
    conflict_of_interest = serializers.BooleanField(required=False)
    answers = serializers.DictField(child=serializers.DictField())


class TaskSerializer(serializers.ModelSerializer):
    assignee_name = serializers.CharField(source="assignee.full_name", read_only=True)
    job_title = serializers.CharField(source="job.title", read_only=True, default=None)
    candidate_name = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "description",
            "status",
            "priority",
            "due_at",
            "assignee",
            "assignee_name",
            "job",
            "job_title",
            "application",
            "candidate_name",
            "is_overdue",
            "completed_at",
            "created_at",
        ]

    def get_candidate_name(self, obj):
        return str(obj.application.candidate) if obj.application_id else None


class TaskWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ["title", "description", "due_at", "priority", "status", "assignee", "job", "application"]


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.Status.choices)
