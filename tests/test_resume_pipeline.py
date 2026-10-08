"""The resume ingestion pipeline (`apps/candidates/tasks.py`).

Uploading a resume is the first thing a candidate does, so this is the first impression the
product makes -- and it was the least covered module outside the environment-dependent ones.
Within it, the branches that had never run were precisely the ones that decide what the
recruiter *sees*: the already-parsed short circuit, the infected-file quarantine, the skill
taxonomy link, the failure path, and duplicate detection.

Two of those matter more than their line count suggests. The failure path is the only thing
standing between a malformed upload and a resume stuck at `UPLOADED` forever with no reason
recorded. And duplicate detection is a headline feature that no test had ever executed.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document

from apps.candidates.models import Candidate, CandidateSkill, DuplicateSuggestion, Resume
from apps.candidates.tasks import process_resume
from apps.jobs.models import Skill

ORG = "northstar-labs"

RESUME_TEXT = (
    "Ada Lovelace\nada.lovelace@example.com\n+62 21 5550 100\n\n"
    "Summary:\nAnalytical engineer with a decade of backend work across payments and\n"
    "logistics platforms. Comfortable owning a service end to end, from schema design\n"
    "through on-call. Prefers boring technology and measurable outcomes.\n\n"
    "Skills:\nPython, Django, PostgreSQL, Redis\n\n"
    "Experience:\nStaff Engineer at Northwind, 2019 to present.\n"
    "Senior Engineer at Meridian, 2015 to 2019.\n\n"
    "Education:\nBSc Computer Science, University of Jakarta.\n"
)


def _docx_bytes(text: str) -> bytes:
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _candidate(organization, email, first="Ada", last="Lovelace", phone=""):
    candidate = Candidate.objects.create(
        organization=organization,
        first_name=first,
        last_name=last,
        city="Jakarta",
        country_code="ID",
        total_experience_months=120,
        education_level="bachelor",
    )
    candidate.set_email(email)
    if phone:
        candidate.set_phone(phone)
    # `set_email` / `set_phone` only populate the encrypted fields and hashes; persisting them
    # is the caller's job.
    candidate.save()
    return candidate


def _resume(organization, candidate, *, data=None, name="resume.docx", checksum=None):
    payload = data if data is not None else _docx_bytes(RESUME_TEXT)
    return Resume.objects.create(
        organization=organization,
        candidate=candidate,
        file=SimpleUploadedFile(name, payload, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        original_name=name,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(payload),
        checksum_sha256=checksum or hashlib.sha256(payload).hexdigest(),
    )


# ------------------------------------------------------------------------- short circuit


@pytest.mark.django_db
def test_process_resume_is_a_noop_when_already_parsed(demo):
    """Re-delivering the same task must not re-parse -- and must not re-read the file."""
    resume = _resume(demo, _candidate(demo, "done@example.com"))
    resume.parser_status = Resume.ParserStatus.COMPLETED
    resume.save(update_fields=["parser_status"])

    assert process_resume(resume.id) == Resume.ParserStatus.COMPLETED
    resume.refresh_from_db()
    assert resume.raw_text == ""


# ----------------------------------------------------------------------------- quarantine


@pytest.mark.django_db
def test_process_resume_quarantines_an_infected_file(demo, monkeypatch):
    """A failed scan must stop the pipeline and say so, never fall through to parsing."""
    import apps.candidates.tasks as tasks

    class Infected:
        def scan(self, stream):
            stream.read()
            return False

    monkeypatch.setattr(tasks, "get_malware_scanner", lambda: Infected())
    resume = _resume(demo, _candidate(demo, "infected@example.com"))

    assert process_resume(resume.id) == Resume.ParserStatus.INFECTED

    resume.refresh_from_db()
    assert resume.parser_status == Resume.ParserStatus.INFECTED
    assert resume.raw_text == ""
    assert resume.parse_results.count() == 0


# -------------------------------------------------------------------------- happy path


@pytest.mark.django_db
def test_process_resume_links_matching_skills_from_the_taxonomy(demo):
    """The extracted skills are what drive match scoring; an unlinked skill scores nothing."""
    for name in ("Python", "Django", "PostgreSQL", "Redis", "Go"):
        Skill.objects.get_or_create(organization=demo, name=name)
    candidate = _candidate(demo, "skilled@example.com")
    resume = _resume(demo, candidate)

    status = process_resume(resume.id)

    assert status == Resume.ParserStatus.COMPLETED
    resume.refresh_from_db()
    assert resume.raw_text
    assert set(
        CandidateSkill.objects.filter(candidate=candidate).values_list("skill__name", flat=True)
    ) == {"Python", "Django", "PostgreSQL", "Redis"}
    assert resume.parse_results.count() == 1


@pytest.mark.django_db
def test_process_resume_flags_a_sparse_document_for_review(demo):
    """A scan-like document yields almost no text; the recruiter must be told to check it."""
    Skill.objects.get_or_create(organization=demo, name="Python")
    resume = _resume(
        demo,
        _candidate(demo, "sparse@example.com"),
        data=_docx_bytes("Ada\nada@example.com"),
        name="scan.docx",
    )

    assert process_resume(resume.id) == Resume.ParserStatus.REVIEW_REQUIRED

    resume.refresh_from_db()
    assert resume.parse_results.first().warnings


# -------------------------------------------------------------------------- failure path


@pytest.mark.django_db
def test_process_resume_records_the_reason_and_reraises(demo, monkeypatch):
    """A broken upload must end as FAILED with a reason, not silently stuck at UPLOADED."""
    import apps.candidates.tasks as tasks

    def explode(data, filename):
        raise ValueError("Corrupt document.")

    monkeypatch.setattr(tasks, "extract_text", explode)
    resume = _resume(demo, _candidate(demo, "broken@example.com"))

    with pytest.raises(ValueError, match="Corrupt document."):
        process_resume(resume.id)

    resume.refresh_from_db()
    assert resume.parser_status == Resume.ParserStatus.FAILED
    assert resume.failure_reason == "Corrupt document."
    assert resume.parse_results.count() == 0


# ---------------------------------------------------------------------- duplicate detection


@pytest.mark.django_db
def test_process_resume_flags_a_duplicate_by_identical_resume(demo):
    """Same file uploaded twice under two names is the classic duplicate application."""
    payload = _docx_bytes(RESUME_TEXT)
    original = _candidate(demo, "first.upload@example.com")
    _resume(demo, original, data=payload)
    duplicate = _candidate(demo, "second.upload@example.com")
    second = _resume(demo, duplicate, data=payload)

    process_resume(second.id)

    suggestion = DuplicateSuggestion.objects.filter(candidate=duplicate).first()
    assert suggestion is not None
    assert suggestion.possible_duplicate_id == original.id
    assert "resume_checksum" in suggestion.signals


@pytest.mark.django_db
def test_process_resume_flags_a_duplicate_by_phone_number(demo):
    """A different resume but the same phone number is still the same person."""
    Skill.objects.get_or_create(organization=demo, name="Python")
    original = _candidate(demo, "phone.one@example.com", phone="+62 21 5550 100")
    duplicate = _candidate(demo, "phone.two@example.com", phone="+62 21 5550 100")
    resume = _resume(demo, duplicate)

    process_resume(resume.id)

    suggestion = DuplicateSuggestion.objects.filter(candidate=duplicate).first()
    assert suggestion is not None
    assert suggestion.possible_duplicate_id == original.id
    assert suggestion.signals.get("phone") == "exact"


@pytest.mark.django_db
def test_process_resume_does_not_flag_unrelated_candidates(demo):
    """Duplicate detection must not cry wolf, or the merge queue becomes noise."""
    Skill.objects.get_or_create(organization=demo, name="Python")
    _candidate(demo, "unrelated.one@example.com", first="Grace", last="Hopper", phone="+62 21 1111 111")
    other = _candidate(demo, "unrelated.two@example.com", first="Alan", last="Turing", phone="+62 21 2222 222")
    resume = _resume(demo, other)

    process_resume(resume.id)

    assert DuplicateSuggestion.objects.filter(candidate=other).count() == 0


@pytest.mark.django_db
def test_process_resume_recomputes_the_match_score_for_linked_applications(demo):
    """A freshly parsed resume changes the score; the pipeline view must show it immediately."""
    from apps.applications.models import Application

    Skill.objects.get_or_create(organization=demo, name="Python")
    application = Application.objects.filter(organization=demo).first()
    assert application is not None
    resume = _resume(demo, application.candidate)
    application.resume = resume
    application.save(update_fields=["resume"])

    process_resume(resume.id)

    assert hasattr(application, "match_score")
    assert application.match_score.total_score is not None
