from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from docx import Document
from pypdf import PdfReader

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
SECTION_RE = re.compile(
    r"^(experience|work experience|employment|education|skills|technical skills|summary|profile)\s*:?$",
    re.IGNORECASE,
)


@dataclass
class ParsedResume:
    contact: dict = field(default_factory=dict)
    summary: str = ""
    experiences: list[dict] = field(default_factory=list)
    educations: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    confidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def extract_text(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        document = Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise ValueError("Only PDF and DOCX resumes are supported.")


def parse_resume_text(text: str, known_skills: list[str]) -> ParsedResume:
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    result = ParsedResume()
    email = EMAIL_RE.search(clean)
    phone = PHONE_RE.search(clean)
    result.contact = {
        "email": email.group(0) if email else "",
        "phone": phone.group(0) if phone else "",
    }
    result.links = list(dict.fromkeys(URL_RE.findall(clean)))[:10]
    lower_text = clean.casefold()
    result.skills = sorted(
        skill for skill in known_skills if re.search(rf"(?<!\w){re.escape(skill.casefold())}(?!\w)", lower_text)
    )
    sections: dict[str, list[str]] = {}
    current = "profile"
    for line in clean.splitlines():
        heading = SECTION_RE.match(line)
        if heading:
            current = heading.group(1).casefold()
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    summary_lines = sections.get("summary") or sections.get("profile") or []
    result.summary = " ".join(summary_lines[:4])[:1000]
    result.confidence = {
        "contact": 0.95 if email else 0.35,
        "skills": 0.9 if result.skills else 0.45,
        "sections": 0.8 if len(sections) > 2 else 0.4,
    }
    if len(clean) < 200:
        result.warnings.append("Very little text was extracted; this may be a scanned document.")
    if not email:
        result.warnings.append("No email address was detected.")
    if not result.skills:
        result.warnings.append("No skills matched the configured taxonomy.")
    return result
