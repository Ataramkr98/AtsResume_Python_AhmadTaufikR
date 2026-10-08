from __future__ import annotations

from decimal import Decimal

from apps.common.ai_gateway import chat_json, narrative_enabled
from apps.jobs.models import JobSkillRequirement, Location

FORMULA_VERSION = "rules-v1"


def _ai_summary(job, candidate, components: dict, total) -> str | None:
    """Optional LLM-written rationale. Never raises and never alters a score."""
    import json

    try:
        data = chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You summarise candidate-job fit for a recruiter. Use only "
                        "the supplied data; never invent facts, never recommend "
                        "hiring or rejecting, and keep it under 60 words. Return "
                        "JSON with a single key 'summary'."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "job_title": getattr(job, "title", ""),
                            "total_score": float(total),
                            "components": components,
                            "matched": sorted(set(str(s) for s in candidate_skills_names(candidate)))[:40],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            feature="ANALYZE",
        )
    except Exception:  # noqa: BLE001 - a narrative is optional, never fatal
        return None
    text = data.get("summary")
    return str(text)[:600] if text else None


def candidate_skills_names(candidate) -> list[str]:
    return list(
        candidate.candidate_skills.select_related("skill").values_list("skill__name", flat=True)
    )
BASE_WEIGHTS = {"skills": 55, "experience": 25, "education": 10, "location": 10}
EDUCATION_LEVELS = {"": 0, "high_school": 1, "associate": 2, "bachelor": 3, "master": 4, "doctorate": 5}


def calculate_match(application):
    job = application.job
    candidate = application.candidate
    requirements = list(job.skill_requirements.select_related("skill"))
    candidate_skills = {
        row.skill.normalized_name for row in candidate.candidate_skills.select_related("skill").all()
    }
    configured: dict[str, float] = {}
    explanation: dict = {"matched": [], "missing": [], "warnings": []}

    if requirements:
        must = [r.skill for r in requirements if r.requirement_type == JobSkillRequirement.RequirementType.MUST]
        preferred = [
            r.skill for r in requirements if r.requirement_type == JobSkillRequirement.RequirementType.PREFERRED
        ]

        def coverage(skills):
            if not skills:
                return None
            found = [skill for skill in skills if skill.normalized_name in candidate_skills]
            explanation["matched"].extend(skill.name for skill in found)
            explanation["missing"].extend(skill.name for skill in skills if skill not in found)
            return len(found) / len(skills)

        must_score, preferred_score = coverage(must), coverage(preferred)
        if must_score is None:
            skill_score = preferred_score or 0
        elif preferred_score is None:
            skill_score = must_score
        else:
            skill_score = must_score * 0.7 + preferred_score * 0.3
        configured["skills"] = skill_score * 100

    if job.min_experience_months is not None:
        actual = candidate.total_experience_months or 0
        configured["experience"] = min(actual / max(job.min_experience_months, 1), 1) * 100
        if candidate.total_experience_months is None:
            explanation["warnings"].append("Candidate experience is unknown.")

    if job.education_level:
        required = EDUCATION_LEVELS.get(job.education_level, 0)
        actual = EDUCATION_LEVELS.get(candidate.education_level, 0)
        configured["education"] = 100 if actual >= required else 0
        if not candidate.education_level:
            explanation["warnings"].append("Candidate education level is unknown.")

    if job.location:
        if job.location.workplace_type == Location.WorkplaceType.REMOTE:
            configured["location"] = 100
        elif candidate.city and candidate.city.casefold() == job.location.city.casefold():
            configured["location"] = 100
        elif candidate.country_code and candidate.country_code.casefold() == job.location.country_code.casefold():
            configured["location"] = 75
        else:
            configured["location"] = 0

    denominator = sum(BASE_WEIGHTS[key] for key in configured) or 1
    total = sum(configured[key] * BASE_WEIGHTS[key] for key in configured) / denominator
    explanation["formula_version"] = FORMULA_VERSION
    explanation["disclaimer"] = "This score supports review and must not be used as an automatic hiring decision."
    if narrative_enabled():
        explanation["ai_summary"] = _ai_summary(job, candidate, configured, total)
    return Decimal(str(round(total, 2))), {key: round(value, 2) for key, value in configured.items()}, explanation
