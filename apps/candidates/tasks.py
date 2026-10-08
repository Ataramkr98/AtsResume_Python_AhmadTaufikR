from __future__ import annotations

from difflib import SequenceMatcher

from celery import shared_task
from django.db import transaction

from apps.common.storage import get_malware_scanner, stream_bytes
from apps.jobs.models import Skill

from .models import CandidateSkill, DuplicateSuggestion, Resume, ResumeParseResult
from .parsing import extract_text, parse_resume_text


@shared_task(bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def process_resume(self, resume_id: str):
    resume = Resume.objects.select_related("candidate", "organization").get(id=resume_id)
    if resume.parser_status in {Resume.ParserStatus.COMPLETED, Resume.ParserStatus.REVIEW_REQUIRED}:
        return resume.parser_status
    try:
        resume.parser_status = Resume.ParserStatus.SCANNING
        resume.failure_reason = ""
        resume.save(update_fields=["parser_status", "failure_reason", "updated_at"])
        with resume.file.open("rb") as source:
            data = source.read()
        if not get_malware_scanner().scan(stream_bytes(data)):
            resume.parser_status = Resume.ParserStatus.INFECTED
            resume.save(update_fields=["parser_status", "updated_at"])
            return resume.parser_status
        resume.parser_status = Resume.ParserStatus.EXTRACTING
        resume.save(update_fields=["parser_status", "updated_at"])
        text = extract_text(data, resume.original_name)
        resume.raw_text = text
        resume.parser_status = Resume.ParserStatus.PARSING
        resume.save(update_fields=["raw_text", "parser_status", "updated_at"])
        skills = list(
            Skill.objects.filter(organization=resume.organization).values_list("name", flat=True)
        )
        parsed = parse_resume_text(text, skills)
        with transaction.atomic():
            ResumeParseResult.objects.create(
                resume=resume,
                parser_version=resume.parser_version,
                payload=parsed.to_dict(),
                confidence=parsed.confidence,
                warnings=parsed.warnings,
            )
            for skill_name in parsed.skills:
                skill = Skill.objects.get(organization=resume.organization, normalized_name=skill_name.casefold())
                CandidateSkill.objects.update_or_create(
                    candidate=resume.candidate,
                    skill=skill,
                    source="RESUME",
                    defaults={"confidence": parsed.confidence.get("skills", 0.8)},
                )
            _create_duplicate_suggestions(resume)
            resume.parser_status = (
                Resume.ParserStatus.REVIEW_REQUIRED if parsed.warnings else Resume.ParserStatus.COMPLETED
            )
            resume.save(update_fields=["parser_status", "updated_at"])
        for application in resume.application_set.all():
            from apps.applications.services import recompute_match_score

            recompute_match_score(application)
        return resume.parser_status
    except Exception as exc:
        resume.parser_status = Resume.ParserStatus.FAILED
        resume.failure_reason = str(exc)[:255]
        resume.save(update_fields=["parser_status", "failure_reason", "updated_at"])
        raise


def _create_duplicate_suggestions(resume: Resume):
    candidate = resume.candidate
    possible = candidate.__class__.objects.filter(
        organization=candidate.organization, deleted_at__isnull=True
    ).exclude(id=candidate.id)
    for other in possible[:100]:
        signals = {}
        score = 0
        if candidate.phone_hash and candidate.phone_hash == other.phone_hash:
            signals["phone"] = "exact"
            score += 70
        if other.resumes.filter(checksum_sha256=resume.checksum_sha256).exists():
            signals["resume_checksum"] = "exact"
            score += 90
        similarity = SequenceMatcher(None, candidate.full_name_normalized, other.full_name_normalized).ratio()
        if similarity >= 0.85:
            signals["name_similarity"] = round(similarity, 3)
            score += similarity * 30
        if score >= 50:
            DuplicateSuggestion.objects.get_or_create(
                organization=candidate.organization,
                candidate=candidate,
                possible_duplicate=other,
                defaults={"score": min(score, 100), "signals": signals},
            )
