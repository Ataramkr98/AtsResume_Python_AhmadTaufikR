from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.accounts.policies import require_permission, scope_candidates
from apps.audit.models import record_audit
from apps.common.crypto import lookup_hash

from .models import Candidate, DuplicateSuggestion, Resume


def validate_resume_upload(upload) -> tuple[bytes, str, str]:
    if upload.size > settings.MAX_RESUME_MB * 1024 * 1024:
        raise ValidationError(f"Resume must not exceed {settings.MAX_RESUME_MB} MB.")
    suffix = Path(upload.name).suffix.casefold()
    data = upload.read()
    if suffix == ".pdf" and data.startswith(b"%PDF"):
        mime = "application/pdf"
    elif suffix == ".docx" and data.startswith(b"PK"):
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise ValidationError("Upload a valid PDF or DOCX file.")
    return data, mime, hashlib.sha256(data).hexdigest()


@transaction.atomic
def create_public_application(*, job, first_name, last_name, email, phone, upload, consent):
    from apps.applications.models import Application, ApplicationStageHistory

    if not consent:
        raise ValidationError("Consent is required.")
    data, mime, checksum = validate_resume_upload(upload)
    email_hash = lookup_hash(email)
    candidate = Candidate.objects.filter(
        organization=job.organization, email_hash=email_hash, deleted_at__isnull=True
    ).first()
    if not candidate:
        candidate = Candidate(
            organization=job.organization,
            first_name=first_name,
            last_name=last_name,
            source_first="CAREERS_PAGE",
        )
        candidate.set_email(email)
        candidate.set_phone(phone)
        candidate.save()
    application = Application.objects.filter(job=job, candidate=candidate).first()
    created = application is None
    if not application:
        first_stage = job.stages.filter(active=True).order_by("position").first()
        if not first_stage:
            raise ValidationError("This job is not accepting applications.")
        application = Application.objects.create(
            organization=job.organization,
            job=job,
            candidate=candidate,
            current_stage=first_stage,
            source="CAREERS_PAGE",
            assigned_recruiter=job.recruiter,
        )
        ApplicationStageHistory.objects.create(
            application=application, to_stage=first_stage, reason="Public application"
        )
    if created:
        resume = Resume(
            organization=job.organization,
            candidate=candidate,
            original_name=upload.name,
            mime_type=mime,
            size_bytes=len(data),
            checksum_sha256=checksum,
        )
        resume.file.save(upload.name, ContentFile(data), save=True)
        application.resume = resume
        application.save(update_fields=["resume", "updated_at"])
        transaction.on_commit(lambda: _enqueue_resume(resume.id))
    return application, created


def _enqueue_resume(resume_id):
    from .tasks import process_resume

    process_resume.delay(str(resume_id))


@transaction.atomic
def create_candidate(*, organization, actor, membership, data):
    """Create a candidate, keeping the encrypted contact fields and lookup hashes in sync."""
    require_permission(membership, "candidates.manage")
    candidate = Candidate(organization=organization, created_by=actor, **_candidate_fields(data))
    candidate.set_email(data["email"])
    candidate.set_phone(data.get("phone", ""))
    candidate.save()
    record_audit(
        organization=organization,
        actor=actor,
        action="candidate.created",
        resource=candidate,
        after={"source": candidate.source_first},
    )
    return candidate


@transaction.atomic
def update_candidate(*, candidate, actor, membership, data):
    """Update the mutable profile fields, re-encrypting contact details when they change."""
    require_permission(membership, "candidates.manage")
    for field, value in _candidate_fields(data).items():
        setattr(candidate, field, value)
    if data.get("email"):
        candidate.set_email(data["email"])
    if "phone" in data:
        candidate.set_phone(data.get("phone") or "")
    candidate.save()
    record_audit(
        organization=candidate.organization,
        actor=actor,
        action="candidate.updated",
        resource=candidate,
        after={"title": candidate.current_title, "company": candidate.current_company},
    )
    return candidate


# Fields a client may set directly. Contact details are deliberately excluded because they are
# stored encrypted with a separate lookup hash and must go through the set_email/set_phone helpers.
CANDIDATE_WRITABLE_FIELDS = (
    "first_name",
    "last_name",
    "city",
    "country_code",
    "current_title",
    "current_company",
    "total_experience_months",
    "education_level",
    "summary",
    "source_first",
)


def _candidate_fields(data: dict) -> dict:
    return {key: data[key] for key in CANDIDATE_WRITABLE_FIELDS if key in data}


@transaction.atomic
def merge_candidates(*, suggestion_id, organization, actor, membership):
    require_permission(membership, "candidates.manage")
    visible_candidates = scope_candidates(membership).values("id")
    suggestion = DuplicateSuggestion.objects.select_for_update().select_related(
        "candidate", "possible_duplicate"
    ).filter(
        id=suggestion_id,
        organization=organization,
        status=DuplicateSuggestion.Status.PENDING,
        candidate_id__in=visible_candidates,
        possible_duplicate_id__in=visible_candidates,
    ).first()
    if not suggestion:
        raise ValidationError("Duplicate suggestion not found.")
    duplicate, master = suggestion.candidate, suggestion.possible_duplicate
    duplicate_jobs = set(duplicate.applications.values_list("job_id", flat=True))
    master_jobs = set(master.applications.values_list("job_id", flat=True))
    if duplicate_jobs & master_jobs:
        raise ValidationError("Archive conflicting applications before merging these candidates.")
    duplicate.resumes.update(candidate=master)
    duplicate.applications.update(candidate=master)
    for row in duplicate.candidate_skills.all():
        if master.candidate_skills.filter(skill=row.skill, source=row.source).exists():
            row.delete()
        else:
            row.candidate = master
            row.save(update_fields=["candidate", "updated_at"])
    duplicate.deleted_at = timezone.now()
    duplicate.save(update_fields=["deleted_at", "updated_at"])
    suggestion.status = DuplicateSuggestion.Status.MERGED
    suggestion.resolved_by = actor
    suggestion.resolved_at = timezone.now()
    suggestion.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
    record_audit(
        organization=organization,
        actor=actor,
        action="candidate.merged",
        resource=master,
        after={"merged_candidate_id": str(duplicate.id)},
    )
    return master
