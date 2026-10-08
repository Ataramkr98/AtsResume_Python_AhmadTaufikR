"""Seed a realistic, deterministic demo workspace.

The command is idempotent: every record is keyed on a natural identifier, so running it twice
produces the same workspace instead of duplicating rows. It writes real files for resumes so the
download flow is exercised, and it drives interviews and scorecards through the same service
functions the web UI uses, which keeps the audit trail and business rules honest.

Usage::

    python manage.py seed_demo
    python manage.py seed_demo --reset     # delete and rebuild the demo workspace
"""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Invitation, Organization, OrganizationMembership, User
from apps.applications.models import Application, ApplicationStageHistory, RejectionReason
from apps.applications.services import recompute_match_score
from apps.audit.models import AuditLog, record_audit
from apps.candidates.models import Candidate, CandidateSkill, Resume, ResumeParseResult
from apps.common.crypto import lookup_hash
from apps.interviews.models import Interview, InterviewParticipant, ScorecardSubmission
from apps.interviews.services import save_scorecard
from apps.jobs.models import (
    Department,
    Job,
    JobSkillRequirement,
    JobTeamMember,
    Location,
    PipelineTemplate,
    PipelineTemplateStage,
    ScorecardTemplate,
    Skill,
)
from apps.jobs.services import create_job, transition_job
from apps.tasks.models import Task

ORGANIZATION_SLUG = "northstar-labs"
ORGANIZATION_NAME = "Northstar Labs"
SEED = 20260912

STAFF = [
    # email, full name, role, title
    ("demo@example.com", "Dana Reyes", "ORG_ADMIN", "Head of Talent Acquisition"),
    ("jordan.lee@northstar.example", "Jordan Lee", "HR_ADMIN", "People Operations Lead"),
    ("maya.chen@northstar.example", "Maya Chen", "RECRUITER", "Technical Recruiter"),
    ("ravi.patel@northstar.example", "Ravi Patel", "HIRING_MANAGER", "Engineering Manager"),
    ("elena.rossi@northstar.example", "Elena Rossi", "INTERVIEWER", "Staff Engineer"),
    ("sam.okafor@northstar.example", "Sam Okafor", "INTERVIEWER", "Principal Designer"),
    ("nina.alvarez@northstar.example", "Nina Alvarez", "AUDITOR", "Compliance Analyst"),
]

DEPARTMENTS = ["Engineering", "Product", "Design", "Data", "People Operations", "Revenue"]

LOCATIONS = [
    ("Jakarta · Hybrid", "Jakarta", "ID", "HYBRID"),
    ("Singapore · On-site", "Singapore", "SG", "ONSITE"),
    ("Remote · APAC", "Jakarta", "ID", "REMOTE"),
    ("Berlin · On-site", "Berlin", "DE", "ONSITE"),
]

SKILLS = [
    ("Python", "Engineering"),
    ("Django", "Engineering"),
    ("REST APIs", "Engineering"),
    ("PostgreSQL", "Engineering"),
    ("Redis", "Engineering"),
    ("Docker", "Engineering"),
    ("Kubernetes", "Engineering"),
    ("CI/CD", "Engineering"),
    ("AWS", "Engineering"),
    ("Terraform", "Engineering"),
    ("TypeScript", "Engineering"),
    ("React", "Engineering"),
    ("Vue", "Engineering"),
    ("Accessibility", "Design"),
    ("Figma", "Design"),
    ("Design Systems", "Design"),
    ("User Research", "Design"),
    ("SQL", "Data"),
    ("dbt", "Data"),
    ("Airflow", "Data"),
    ("Looker", "Data"),
    ("Team Leadership", "Leadership"),
    ("Stakeholder Management", "Leadership"),
    ("Product Strategy", "Product"),
]

# Pipeline stages: name, stage type, requires interview, requires scorecard, terminal, SLA hours
PIPELINE_STAGES = [
    ("Applied", "APPLIED", False, False, False, 48),
    ("Screening", "SCREENING", False, False, False, 120),
    ("Interview", "INTERVIEW", True, False, False, 168),
    ("Assessment", "ASSESSMENT", False, True, False, 120),
    ("Offer", "OFFER", False, False, False, 96),
    ("Hired", "HIRED", False, False, True, None),
]

# title, department, location, employment type, headcount, status, min months, education, skills
JOBS = [
    (
        "Senior Backend Engineer", "Engineering", "Jakarta · Hybrid", "FULL_TIME", 2, "OPEN", 48, "bachelor",
        [("Python", "MUST"), ("Django", "MUST"), ("PostgreSQL", "PREFERRED"), ("Docker", "PREFERRED")],
        "<p>Own the services that power hiring for hundreds of teams.</p><ul><li>Design and ship Django services end to end</li><li>Partner with product and design on the roadmap</li><li>Raise the bar on testing, observability and code review</li></ul>",
        "<ul><li>4+ years building production Python services</li><li>Comfortable with SQL, caching and background jobs</li><li>Clear written communication</li></ul>",
    ),
    (
        "Product Designer", "Design", "Remote · APAC", "FULL_TIME", 1, "OPEN", 36, "bachelor",
        [("Figma", "MUST"), ("Design Systems", "MUST"), ("User Research", "PREFERRED"), ("Accessibility", "PREFERRED")],
        "<p>Shape how recruiters and candidates experience hiring.</p><ul><li>Lead design from discovery to polished UI</li><li>Maintain and extend our design system</li><li>Run research with recruiters and hiring managers</li></ul>",
        "<ul><li>Portfolio of shipped product work</li><li>Strong systems thinking and craft</li><li>Experience working with engineering partners</li></ul>",
    ),
    (
        "Data Analyst", "Data", "Singapore · On-site", "FULL_TIME", 1, "OPEN", 24, "bachelor",
        [("SQL", "MUST"), ("dbt", "PREFERRED"), ("Looker", "PREFERRED")],
        "<p>Turn hiring data into decisions leaders trust.</p><ul><li>Model funnel and velocity metrics in dbt</li><li>Build dashboards hiring managers actually open</li><li>Investigate anomalies before they reach the exec review</li></ul>",
        "<ul><li>Advanced SQL and dimensional modelling</li><li>Experience with a BI tool</li><li>Comfortable presenting to non-technical stakeholders</li></ul>",
    ),
    (
        "Engineering Manager", "Engineering", "Jakarta · Hybrid", "FULL_TIME", 1, "OPEN", 72, "bachelor",
        [("Team Leadership", "MUST"), ("Python", "MUST"), ("Kubernetes", "PREFERRED"), ("Stakeholder Management", "PREFERRED")],
        "<p>Grow a healthy, high-trust engineering team.</p><ul><li>Coach engineers through growth and delivery</li><li>Own the technical roadmap with product</li><li>Keep hiring and onboarding predictable</li></ul>",
        "<ul><li>6+ years engineering, 2+ managing</li><li>Track record of shipping and retaining teams</li><li>Strong written communication</li></ul>",
    ),
    (
        "Frontend Engineer", "Engineering", "Remote · APAC", "FULL_TIME", 1, "PAUSED", 36, "bachelor",
        [("TypeScript", "MUST"), ("React", "MUST"), ("Accessibility", "PREFERRED"), ("Vue", "PREFERRED")],
        "<p>Build fast, accessible interfaces for hiring teams.</p><ul><li>Ship TypeScript features with strong a11y defaults</li><li>Own performance budgets for core screens</li><li>Collaborate closely with design</li></ul>",
        "<ul><li>3+ years with a modern component framework</li><li>Care for accessibility and semantics</li><li>Comfortable with build tooling</li></ul>",
    ),
    (
        "Solutions Engineer", "Revenue", "Berlin · On-site", "FULL_TIME", 1, "CLOSED", 48, "bachelor",
        [("Python", "MUST"), ("Stakeholder Management", "MUST"), ("AWS", "PREFERRED"), ("Docker", "PREFERRED")],
        "<p>Help enterprise customers adopt the platform.</p><ul><li>Run technical discovery and proof of value</li><li>Build reference integrations and scripts</li><li>Feed product requirements back from the field</li></ul>",
        "<ul><li>4+ years in a customer-facing technical role</li><li>Working knowledge of Python and cloud infrastructure</li><li>Excellent presentation skills</li></ul>",
    ),
]

# first, last, title, company, city, country, experience months, education, source, skills
CANDIDATES = [
    ("Nadia", "Putri", "Backend Engineer", "Tokopedia", "Jakarta", "ID", 72, "bachelor", "REFERRAL", ["Python", "Django", "PostgreSQL"]),
    ("Rafi", "Hidayat", "Software Engineer", "Gojek", "Jakarta", "ID", 48, "bachelor", "CAREERS_PAGE", ["Python", "REST APIs", "Docker"]),
    ("Sarah", "Wijaya", "Platform Engineer", "Traveloka", "Jakarta", "ID", 84, "bachelor", "LINKEDIN", ["Python", "Redis", "Docker", "Kubernetes"]),
    ("Dimas", "Pratama", "Full-stack Engineer", "Bukalapak", "Bandung", "ID", 54, "bachelor", "CAREERS_PAGE", ["Django", "TypeScript", "PostgreSQL"]),
    ("Aisha", "Rahman", "Senior Backend Engineer", "Grab", "Singapore", "SG", 96, "master", "LINKEDIN", ["Python", "Django", "AWS", "Kubernetes"]),
    ("Kevin", "Tan", "Backend Developer", "Shopee", "Singapore", "SG", 60, "bachelor", "REFERRAL", ["Python", "PostgreSQL", "Redis"]),
    ("Lina", "Nguyen", "Software Engineer", "Sea Group", "Singapore", "SG", 42, "bachelor", "CAREERS_PAGE", ["Python", "Django", "CI/CD"]),
    ("Arjun", "Mehta", "Staff Engineer", "Atlassian", "Singapore", "SG", 132, "bachelor", "LINKEDIN", ["Python", "Kubernetes", "Terraform", "AWS"]),
    ("Putri", "Handayani", "Backend Engineer", "Halodoc", "Jakarta", "ID", 66, "bachelor", "AGENCY", ["Django", "PostgreSQL", "Docker"]),
    ("Bagus", "Santoso", "Software Engineer", "Blibli", "Jakarta", "ID", 39, "bachelor", "CAREERS_PAGE", ["Python", "REST APIs"]),
    ("Clara", "Sutanto", "Backend Engineer", "Xendit", "Jakarta", "ID", 51, "bachelor", "REFERRAL", ["Python", "Django", "Redis"]),
    ("Yusuf", "Ibrahim", "Data Analyst", "DANA", "Jakarta", "ID", 36, "bachelor", "CAREERS_PAGE", ["SQL", "Looker"]),
    ("Mei", "Lin", "Senior Data Analyst", "Lazada", "Singapore", "SG", 72, "master", "LINKEDIN", ["SQL", "dbt", "Looker"]),
    ("Rina", "Kusuma", "Data Analyst", "Bank Mandiri", "Jakarta", "ID", 30, "bachelor", "CAREERS_PAGE", ["SQL", "Python"]),
    ("Hendra", "Gunawan", "Analytics Engineer", "Tokopedia", "Jakarta", "ID", 58, "bachelor", "REFERRAL", ["SQL", "dbt", "Airflow"]),
    ("Sofia", "Andersson", "Product Designer", "Spotify", "Berlin", "DE", 84, "bachelor", "LINKEDIN", ["Figma", "Design Systems", "User Research"]),
    ("Tobias", "Weber", "Senior Product Designer", "Zalando", "Berlin", "DE", 96, "bachelor", "REFERRAL", ["Figma", "Design Systems", "Accessibility"]),
    ("Ayu", "Lestari", "UI/UX Designer", "Traveloka", "Jakarta", "ID", 48, "bachelor", "CAREERS_PAGE", ["Figma", "User Research"]),
    ("Michael", "Chang", "Product Designer", "Canva", "Singapore", "SG", 60, "bachelor", "LINKEDIN", ["Figma", "Design Systems"]),
    ("Dewi", "Anggraini", "Product Designer", "Halodoc", "Jakarta", "ID", 42, "bachelor", "AGENCY", ["Figma", "Accessibility"]),
    ("Bambang", "Wijoyo", "Engineering Manager", "Gojek", "Jakarta", "ID", 120, "bachelor", "REFERRAL", ["Team Leadership", "Python", "Kubernetes"]),
    ("Grace", "Lim", "Engineering Manager", "Grab", "Singapore", "SG", 108, "master", "LINKEDIN", ["Team Leadership", "Python", "Stakeholder Management"]),
    ("Fajar", "Nugroho", "Tech Lead", "Traveloka", "Jakarta", "ID", 96, "bachelor", "CAREERS_PAGE", ["Team Leadership", "Python", "AWS"]),
    ("Ivan", "Kurniawan", "Frontend Engineer", "Ruangguru", "Jakarta", "ID", 48, "bachelor", "CAREERS_PAGE", ["TypeScript", "React"]),
    ("Nadine", "Halim", "Frontend Developer", "Tokopedia", "Jakarta", "ID", 36, "bachelor", "REFERRAL", ["TypeScript", "React", "Accessibility"]),
    ("Oscar", "Fernandez", "Frontend Engineer", "Wise", "Berlin", "DE", 66, "bachelor", "LINKEDIN", ["TypeScript", "Vue", "Accessibility"]),
    ("Rachel", "Simbolon", "Frontend Engineer", "Blibli", "Jakarta", "ID", 30, "bachelor", "CAREERS_PAGE", ["React", "TypeScript"]),
    ("Tommy", "Halim", "Solutions Engineer", "Twilio", "Singapore", "SG", 84, "bachelor", "LINKEDIN", ["Python", "AWS", "Stakeholder Management"]),
    ("Veronika", "Novak", "Solutions Engineer", "Stripe", "Berlin", "DE", 72, "bachelor", "REFERRAL", ["Python", "Docker", "Stakeholder Management"]),
    ("Andi", "Saputra", "Solutions Engineer", "AWS", "Jakarta", "ID", 60, "bachelor", "AGENCY", ["Python", "AWS", "Docker"]),
    ("Rizky", "Ramadhan", "Data Analyst", "Tokopedia", "Jakarta", "ID", 42, "bachelor", "CAREERS_PAGE", ["SQL", "Python"]),
    ("Fiona", "Tan", "Business Analyst", "DBS", "Singapore", "SG", 54, "bachelor", "LINKEDIN", ["SQL", "Looker"]),
    ("Yoga", "Pratama", "Data Analyst", "Gojek", "Jakarta", "ID", 33, "bachelor", "REFERRAL", ["SQL", "dbt"]),
    ("Hannah", "Kim", "Analytics Engineer", "Shopee", "Singapore", "SG", 66, "master", "LINKEDIN", ["SQL", "dbt", "Airflow"]),
]

# job index, candidate index (1-based), target stage index, status, applied N days ago
APPLICATIONS = [
    # Senior Backend Engineer
    (0, 1, 5, "HIRED", 78),
    (0, 5, 4, "ACTIVE", 62),
    (0, 3, 3, "ACTIVE", 55),
    (0, 2, 2, "ACTIVE", 40),
    (0, 9, 2, "ACTIVE", 34),
    (0, 10, 1, "ACTIVE", 21),
    (0, 11, 1, "ACTIVE", 18),
    (0, 6, 0, "ACTIVE", 9),
    (0, 7, 0, "ACTIVE", 6),
    (0, 8, 0, "ACTIVE", 3),
    # Product Designer
    (1, 16, 5, "HIRED", 70),
    (1, 17, 4, "ACTIVE", 58),
    (1, 18, 2, "ACTIVE", 36),
    (1, 19, 1, "ACTIVE", 24),
    (1, 20, 0, "ACTIVE", 12),
    # Data Analyst
    (2, 13, 5, "HIRED", 66),
    (2, 12, 3, "ACTIVE", 52),
    (2, 14, 2, "ACTIVE", 38),
    (2, 15, 1, "ACTIVE", 26),
    (2, 31, 1, "ACTIVE", 19),
    (2, 33, 0, "ACTIVE", 11),
    (2, 34, 0, "ACTIVE", 8),
    (2, 32, 0, "ACTIVE", 4),
    # Engineering Manager
    (3, 21, 4, "ACTIVE", 60),
    (3, 22, 3, "ACTIVE", 47),
    (3, 23, 2, "ACTIVE", 33),
    (3, 8, 1, "ACTIVE", 20),
    (3, 5, 0, "ACTIVE", 7),
    # Frontend Engineer
    (4, 24, 2, "ACTIVE", 44),
    (4, 25, 1, "ACTIVE", 29),
    (4, 26, 1, "ACTIVE", 17),
    (4, 27, 0, "ACTIVE", 10),
    # Solutions Engineer
    (5, 28, 5, "HIRED", 95),
    (5, 29, 3, "REJECTED", 61),
    (5, 30, 2, "REJECTED", 49),
    (5, 27, 1, "WITHDRAWN", 30),
]

REJECTION_REASONS = [
    ("Skills mismatch", "QUALIFICATIONS"),
    ("Not enough experience", "QUALIFICATIONS"),
    ("Position filled", "BUSINESS"),
    ("Compensation misalignment", "BUSINESS"),
    ("Withdrew from process", "CANDIDATE"),
]

# title, assignee index into STAFF, status, priority, due offset in days (None = no due date),
# job index (None = none), candidate index (None = none), description
TASKS = [
    ("Send offer letter to Nadia Putri", 0, "OPEN", "URGENT", 1, 0, 1, "Offer approved — issue the letter and confirm the start date."),
    ("Schedule the panel for Mei Lin", 2, "IN_PROGRESS", "HIGH", 2, 2, 13, "Three interviewers needed; check calendar availability first."),
    ("Chase overdue scorecard from Sam", 0, "OPEN", "HIGH", -2, 1, 18, "Assessment scorecard is two days past SLA."),
    ("Refresh the Engineering Manager job description", 3, "OPEN", "NORMAL", 5, 3, None, "Align the description with the updated levelling guide."),
    ("Screen the new Data Analyst applications", 2, "IN_PROGRESS", "NORMAL", 3, 2, None, "Eight applications landed this week."),
    ("Confirm the Berlin onsite logistics", 1, "OPEN", "NORMAL", 9, 5, None, "Book the meeting room and send travel details."),
    ("Archive the Solutions Engineer pipeline", 1, "DONE", "LOW", -12, 5, None, "Role is closed and the hire has started."),
    ("Draft the Q3 hiring plan", 0, "IN_PROGRESS", "HIGH", 7, None, None, "Headcount request is due to the exec team."),
    ("Review the Frontend Engineer requisition", 3, "OPEN", "NORMAL", -1, 4, None, "Decide whether to reopen the paused role."),
    ("Update interview scorecard criteria", 1, "DONE", "NORMAL", -8, None, None, "Add a systems-design competency for senior roles."),
    ("Follow up with Tobias Weber", 2, "OPEN", "HIGH", 0, 1, 17, "Candidate is deciding between two offers."),
    ("Prepare the weekly hiring digest", 0, "CANCELLED", "LOW", -4, None, None, "Superseded by the new reports page."),
]

RESUME_TEMPLATE = """{name}
{title} · {city}, {country}
{email} · +62 811 000 {phone}

SUMMARY
{title} with {years} years of experience building and shipping software in
fast-moving product teams. Currently at {company}.

EXPERIENCE
{company} — {title}
  · Delivered and maintained production systems used daily by customers.
  · Partnered with product and design to scope and ship features.
  · Improved reliability and reduced page latency across core flows.

SKILLS
{skills}
"""


class Command(BaseCommand):
    help = "Create a deterministic organization with realistic demo hiring data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the existing demo organization before rebuilding it.",
        )

    def handle(self, *args, **options):
        self.rng = random.Random(SEED)
        self.now = timezone.now()
        if options["reset"]:
            removed = self._purge()
            self.stdout.write(f"Removed the previous demo workspace ({removed} rows).")

        with transaction.atomic():
            self.organization = self._seed_organization()
            self.users = self._seed_users()
            self.departments = self._seed_departments()
            self.locations = self._seed_locations()
            self.skills = self._seed_skills()
            self.template = self._seed_pipeline_template()
            self.jobs = self._seed_jobs()
            self.candidates = self._seed_candidates()
            self.resumes = self._seed_resumes()
            self.applications = self._seed_applications()
            self._seed_interviews()
            self._seed_tasks()
            self._seed_activity()

        self._report()

    # ------------------------------------------------------------------ helpers

    def _purge(self):
        """Delete the demo workspace in dependency order.

        A plain ``Organization.delete()`` trips the ``PROTECT`` foreign keys that guard hiring
        history (a job may not lose its department, an application may not lose its stage), so we
        remove the protected children first and let cascades finish the job.
        """
        organization = Organization.objects.filter(slug=ORGANIZATION_SLUG).first()
        if not organization:
            return 0
        from apps.applications.models import IdempotencyRecord
        from apps.candidates.models import DuplicateSuggestion

        removed = 0
        # Interviews hang off applications rather than the organization, so they go first.
        removed += Interview.objects.filter(application__organization=organization).delete()[0]
        for model in (
            Task,
            Application,
            Resume,
            Candidate,
            Job,
            Skill,
            PipelineTemplate,
            Department,
            Location,
            AuditLog,
            IdempotencyRecord,
            RejectionReason,
            DuplicateSuggestion,
            OrganizationMembership,
            Invitation,
        ):
            removed += model.objects.filter(organization=organization).delete()[0]
        removed += organization.delete()[0]
        return removed

    def _stamp(self, obj, *, created_at, updated_at=None):
        """Backdate a row so the demo timeline reads like real history."""
        model = type(obj)
        model.objects.filter(pk=obj.pk).update(
            created_at=created_at, updated_at=updated_at or created_at
        )

    def _user(self, email, full_name, *, password, **extra):
        user, created = User.objects.get_or_create(
            email=email, defaults={"full_name": full_name, **extra}
        )
        if created or not user.has_usable_password():
            user.set_password(password)
            user.full_name = full_name
            for key, value in extra.items():
                setattr(user, key, value)
            user.save()
        return user

    def _membership(self, user, role):
        membership, _ = OrganizationMembership.objects.update_or_create(
            organization=self.organization, user=user, defaults={"role": role, "active": True}
        )
        return membership

    # ------------------------------------------------------------------ seeding

    def _seed_organization(self):
        organization, _ = Organization.objects.get_or_create(
            slug=ORGANIZATION_SLUG,
            defaults={
                "name": ORGANIZATION_NAME,
                "timezone": "Asia/Jakarta",
                "job_approval_required": True,
                "blind_feedback": True,
            },
        )
        return organization

    def _seed_users(self):
        password = settings.DEMO_PASSWORD
        users = {}
        for email, full_name, role, _title in STAFF:
            user = self._user(email, full_name, password=password)
            self._membership(user, role)
            users[email] = user
        # A separate superuser keeps the Django admin out of the public demo account.
        admin = self._user(
            "admin@example.com", "Avery Morgan", password="DemoPass123!", is_staff=True, is_superuser=True
        )
        self._membership(admin, "ORG_ADMIN")
        users["admin@example.com"] = admin
        self.memberships = {
            email: OrganizationMembership.objects.get(organization=self.organization, user=user)
            for email, user in users.items()
        }
        return users

    def _seed_departments(self):
        departments = {}
        for name in DEPARTMENTS:
            department, _ = Department.objects.get_or_create(
                organization=self.organization, name=name
            )
            departments[name] = department
        return departments

    def _seed_locations(self):
        locations = {}
        for name, city, country_code, workplace_type in LOCATIONS:
            location, _ = Location.objects.get_or_create(
                organization=self.organization,
                name=name,
                defaults={
                    "city": city,
                    "country_code": country_code,
                    "workplace_type": workplace_type,
                },
            )
            locations[name] = location
        return locations

    def _seed_skills(self):
        skills = {}
        for name, category in SKILLS:
            skill, _ = Skill.objects.get_or_create(
                organization=self.organization,
                normalized_name=name.casefold(),
                defaults={"name": name, "category": category},
            )
            skills[name] = skill
        return skills

    def _seed_pipeline_template(self):
        template, _ = PipelineTemplate.objects.get_or_create(
            organization=self.organization, name="Standard hiring"
        )
        for position, (name, stage_type, requires_interview, requires_scorecard, terminal, sla) in enumerate(
            PIPELINE_STAGES, 1
        ):
            PipelineTemplateStage.objects.update_or_create(
                template=template,
                position=position,
                defaults={
                    "name": name,
                    "stage_type": stage_type,
                    "requires_interview": requires_interview,
                    "requires_scorecard": requires_scorecard,
                    "terminal": terminal,
                    "sla_hours": sla,
                },
            )
        return template

    def _seed_jobs(self):
        admin = self.users["demo@example.com"]
        admin_membership = self.memberships["demo@example.com"]
        recruiter = self.users["maya.chen@northstar.example"]
        manager = self.users["ravi.patel@northstar.example"]
        jobs = []
        for index, (
            title, department, location, employment_type, headcount, status,
            min_months, education, skill_specs, description, requirements,
        ) in enumerate(JOBS):
            hiring_manager = manager if department in {"Engineering", "Data"} else admin
            job = Job.objects.filter(organization=self.organization, slug=_slugify(title)).first()
            if not job:
                job = create_job(
                    organization=self.organization,
                    actor=admin,
                    membership=admin_membership,
                    data={
                        "title": title,
                        "department": self.departments[department],
                        "location": self.locations[location],
                        "hiring_manager": hiring_manager,
                        "recruiter": recruiter,
                        "pipeline_template": self.template,
                        "employment_type": employment_type,
                        "headcount": headcount,
                        "description_html": description,
                        "requirements_html": requirements,
                        "min_experience_months": min_months,
                        "education_level": education,
                    },
                )
                for skill_name, requirement_type in skill_specs:
                    JobSkillRequirement.objects.create(
                        job=job, skill=self.skills[skill_name], requirement_type=requirement_type
                    )
            if status in {"OPEN", "PAUSED", "CLOSED"}:
                self._advance_job(job, status, admin, admin_membership)
            JobTeamMember.objects.get_or_create(
                job=job,
                user=self.users["elena.rossi@northstar.example"],
                defaults={"role": "INTERVIEWER", "permission_level": "REVIEW"},
            )
            JobTeamMember.objects.get_or_create(
                job=job,
                user=self.users["sam.okafor@northstar.example"],
                defaults={"role": "INTERVIEWER", "permission_level": "REVIEW"},
            )
            self._stamp(job, created_at=self.now - timedelta(days=120 - index * 6))
            jobs.append(job)
        return jobs

    def _advance_job(self, job, target_status, actor, membership):
        """Walk a job through the allowed transition graph to reach the demo status.

        Re-running the seeder has to be a no-op, so a requisition already sitting at the
        target status returns immediately. Without that guard a second run tries to walk
        the path again and asks the state machine to reopen a CLOSED job, which it
        correctly refuses -- and because ``compose.yaml`` runs ``seed_demo`` on every
        container start, that would break every redeploy against an existing database.
        """
        paths = {
            "OPEN": ["OPEN"],
            "PAUSED": ["OPEN", "PAUSED"],
            "CLOSED": ["OPEN", "CLOSED"],
        }
        job.refresh_from_db()
        if job.status == target_status:
            return
        for step in paths[target_status]:
            job.refresh_from_db()
            if job.status == step:
                continue
            transition_job(
                job_id=job.id,
                organization=self.organization,
                actor=actor,
                membership=membership,
                target_status=step,
                expected_version=job.version,
            )

    def _seed_candidates(self):
        creator = self.users["maya.chen@northstar.example"]
        candidates = []
        for index, (
            first, last, title, company, city, country, months, education, source, skill_names,
        ) in enumerate(CANDIDATES):
            email = f"{first}.{last}@example.net".casefold()
            candidate = Candidate.objects.filter(
                organization=self.organization, email_hash=lookup_hash(email)
            ).first()
            if not candidate:
                candidate = Candidate(
                    organization=self.organization,
                    first_name=first,
                    last_name=last,
                    current_title=title,
                    current_company=company,
                    city=city,
                    country_code=country,
                    total_experience_months=months,
                    education_level=education,
                    summary=(
                        f"{title} at {company} with {months // 12} years of experience "
                        f"across {', '.join(skill_names[:2])}."
                    ),
                    source_first=source,
                    created_by=creator,
                )
                candidate.set_email(email)
                candidate.set_phone(f"+62811{index:07d}")
                candidate.save()
                self._stamp(candidate, created_at=self.now - timedelta(days=110 - index))
            for skill_name in skill_names:
                CandidateSkill.objects.get_or_create(
                    candidate=candidate,
                    skill=self.skills[skill_name],
                    source="RESUME",
                    defaults={"confidence": 0.9},
                )
            candidates.append(candidate)
        return candidates

    def _seed_resumes(self):
        """Attach a real, downloadable resume file to the first two thirds of the cohort."""
        uploader = self.users["maya.chen@northstar.example"]
        resumes = {}
        for index, candidate in enumerate(self.candidates):
            if index % 3 == 2:
                continue  # a third of candidates have no resume on file, which is realistic
            if candidate.resumes.exists():
                resumes[candidate.id] = candidate.resumes.first()
                continue
            email = candidate.primary_email
            body = RESUME_TEMPLATE.format(
                name=str(candidate),
                title=candidate.current_title or "Professional",
                city=candidate.city or "Jakarta",
                country=candidate.country_code or "ID",
                email=email,
                phone=index,
                years=(candidate.total_experience_months or 24) // 12,
                company=candidate.current_company or "Independent",
                skills=", ".join(
                    row.skill.name for row in candidate.candidate_skills.select_related("skill")
                ),
            )
            payload = body.encode("utf-8")
            # Rotate a few outcomes so the parsing dashboard shows real variety.
            status = ["COMPLETED", "COMPLETED", "COMPLETED", "REVIEW_REQUIRED", "FAILED"][index % 5]
            resume = Resume(
                organization=self.organization,
                candidate=candidate,
                original_name=f"{str(candidate).replace(' ', '-').lower()}-resume.txt",
                mime_type="text/plain",
                size_bytes=len(payload),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                parser_status=status,
                parser_version="deterministic-v1",
                detected_language="en",
                raw_text=body if status != "FAILED" else "",
                failure_reason="No extractable text layer" if status == "FAILED" else "",
                uploaded_by=uploader,
            )
            resume.file.save(resume.original_name, ContentFile(payload), save=False)
            resume.save()
            self._stamp(resume, created_at=self.now - timedelta(days=104 - index))
            if status in {"COMPLETED", "REVIEW_REQUIRED"}:
                ResumeParseResult.objects.create(
                    resume=resume,
                    parser_version="deterministic-v1",
                    payload={
                        "full_name": str(candidate),
                        "email": email,
                        "current_title": candidate.current_title,
                        "current_company": candidate.current_company,
                        "total_experience_months": candidate.total_experience_months,
                        "skills": [
                            row.skill.name
                            for row in candidate.candidate_skills.select_related("skill")
                        ],
                    },
                    confidence={"overall": 0.93, "contact": 0.99, "experience": 0.88},
                    warnings=[] if status == "COMPLETED" else ["Experience dates could not be verified."],
                )
            resumes[candidate.id] = resume
        return resumes

    def _seed_applications(self):
        reasons = {}
        for name, category in REJECTION_REASONS:
            reason, _ = RejectionReason.objects.get_or_create(
                organization=self.organization, name=name, defaults={"category": category}
            )
            reasons[name] = reason
        rejection_cycle = list(reasons.values())

        recruiter = self.users["maya.chen@northstar.example"]
        applications = []
        for index, (job_index, candidate_number, stage_index, status, days_ago) in enumerate(APPLICATIONS):
            job = self.jobs[job_index]
            candidate = self.candidates[candidate_number - 1]
            stages = list(job.stages.order_by("position"))
            target = stages[stage_index]
            applied_at = self.now - timedelta(days=days_ago)

            application, created = Application.objects.get_or_create(
                organization=self.organization,
                job=job,
                candidate=candidate,
                defaults={
                    "resume": self.resumes.get(candidate.id),
                    "current_stage": target,
                    "status": status,
                    "source": candidate.source_first,
                    "assigned_recruiter": recruiter,
                    "applied_at": applied_at,
                    "last_stage_changed_at": applied_at,
                },
            )
            if not created:
                applications.append(application)
                continue

            cursor = applied_at
            ApplicationStageHistory.objects.create(
                application=application,
                from_stage=None,
                to_stage=stages[0],
                moved_by=recruiter,
                reason="Application received",
                moved_at=cursor,
                duration_previous_stage_seconds=0,
            )
            for step in range(1, stage_index + 1):
                # Senior stages take longer; this is what makes the velocity report meaningful.
                dwell_days = self.rng.randint(2, 6) + step
                cursor = cursor + timedelta(days=dwell_days)
                ApplicationStageHistory.objects.create(
                    application=application,
                    from_stage=stages[step - 1],
                    to_stage=stages[step],
                    moved_by=recruiter,
                    reason="Advanced after review",
                    moved_at=cursor,
                    duration_previous_stage_seconds=dwell_days * 86400,
                )

            application.last_stage_changed_at = cursor
            if status == "HIRED":
                application.hired_at = cursor
            elif status == "REJECTED":
                application.rejected_at = cursor + timedelta(days=2)
                application.rejection_reason = rejection_cycle[index % len(rejection_cycle)]
            application.save()
            self._stamp(application, created_at=applied_at, updated_at=cursor)
            recompute_match_score(application)
            record_audit(
                organization=self.organization,
                actor=recruiter,
                action="application.created",
                resource=application,
                after={"job": job.title, "source": application.source},
            )
            applications.append(application)
        return applications

    def _seed_interviews(self):
        """Reuse the real scheduling and scorecard services so rules and audits stay honest."""
        interviewer_emails = ["elena.rossi@northstar.example", "sam.okafor@northstar.example"]
        completed_pool = [
            self.users["elena.rossi@northstar.example"],
            self.users["sam.okafor@northstar.example"],
        ]
        scheduled_offset = 1

        for application in self.applications:
            stages = list(application.job.stages.order_by("position"))
            stage_index = stages.index(application.current_stage)
            if stage_index < 2:
                continue
            template = ScorecardTemplate.objects.filter(job=application.job, active=True).first()
            if not template:
                continue

            if application.status == "ACTIVE" and stage_index == 2:
                start = self.now + timedelta(days=scheduled_offset, hours=3)
                scheduled_offset += 3
                interview, created = Interview.objects.get_or_create(
                    application=application,
                    title=f"Technical interview — {application.candidate}",
                    defaults={
                        "scorecard_template": template,
                        "template_snapshot": _snapshot(template),
                        "interview_type": "VIDEO",
                        "start_at": start,
                        "end_at": start + timedelta(hours=1),
                        "timezone": "Asia/Jakarta",
                        "meeting_url": "https://meet.example.com/northstar-interview",
                        "status": Interview.Status.SCHEDULED,
                        "created_by": self.users["maya.chen@northstar.example"],
                    },
                )
                if created:
                    for email in interviewer_emails:
                        InterviewParticipant.objects.get_or_create(
                            interview=interview, user=self.users[email], defaults={"role": "INTERVIEWER"}
                        )
                    record_audit(
                        organization=self.organization,
                        actor=self.users["maya.chen@northstar.example"],
                        action="interview.scheduled",
                        resource=interview,
                    )
                continue

            # Everything that progressed past the interview stage has completed feedback.
            interview, created = Interview.objects.get_or_create(
                application=application,
                title=f"Technical interview — {application.candidate}",
                defaults={
                    "scorecard_template": template,
                    "template_snapshot": _snapshot(template),
                    "interview_type": "VIDEO",
                    "start_at": application.last_stage_changed_at - timedelta(days=4),
                    "end_at": application.last_stage_changed_at - timedelta(days=4) + timedelta(hours=1),
                    "timezone": "Asia/Jakarta",
                    "meeting_url": "https://meet.example.com/northstar-interview",
                    "status": Interview.Status.COMPLETED,
                    "created_by": self.users["maya.chen@northstar.example"],
                },
            )
            if not created:
                continue
            for email in interviewer_emails:
                InterviewParticipant.objects.get_or_create(
                    interview=interview, user=self.users[email], defaults={"role": "INTERVIEWER"}
                )
            record_audit(
                organization=self.organization,
                actor=self.users["maya.chen@northstar.example"],
                action="interview.scheduled",
                resource=interview,
            )
            self._stamp(
                interview,
                created_at=interview.start_at - timedelta(days=3),
                updated_at=interview.end_at,
            )

            criteria = interview.template_snapshot.get("criteria", [])
            # Two panellists submit, so completion is high but not a fake 100%.
            for interviewer in completed_pool:
                if not interviewer:
                    continue
                answers = {}
                for criterion in criteria:
                    rating = self.rng.choice([3, 4, 4, 5])
                    answers[criterion["id"]] = {
                        "rating": rating,
                        "evidence": _EVIDENCE.get(criterion["competency"], _EVIDENCE["default"]),
                    }
                try:
                    save_scorecard(
                        interview=interview,
                        actor=interviewer,
                        recommendation=self.rng.choice(["STRONG_YES", "YES", "YES", "MIXED"]),
                        summary=(
                            f"{interviewer.full_name.split(' ')[0]} reviewed the {application.job.title} "
                            "interview and captured evidence against every competency."
                        ),
                        answers=answers,
                        submit=True,
                    )
                except Exception:  # noqa: BLE001 - a demo seed must never fail on one scorecard
                    continue
            if not interview.scorecard_submissions.exists():
                ScorecardSubmission.objects.create(
                    interview=interview,
                    application=application,
                    submitted_by=self.users["elena.rossi@northstar.example"],
                    template_version=template.version,
                    status=ScorecardSubmission.Status.DRAFT,
                )

    def _seed_tasks(self):
        creator = self.users["demo@example.com"]
        for (
            title, staff_index, status, priority, due_offset, job_index, candidate_number, description,
        ) in TASKS:
            assignee = self.users[STAFF[staff_index][0]]
            job = self.jobs[job_index] if job_index is not None else None
            application = None
            if candidate_number is not None:
                candidate = self.candidates[candidate_number - 1]
                application = next(
                    (row for row in self.applications if row.candidate_id == candidate.id and (job is None or row.job_id == job.id)),
                    None,
                )
            due_at = self.now + timedelta(days=due_offset) if due_offset is not None else None
            task, created = Task.objects.get_or_create(
                organization=self.organization,
                title=title,
                assignee=assignee,
                defaults={
                    "description": description,
                    "due_at": due_at,
                    "priority": priority,
                    "status": status,
                    "job": job,
                    "application": application,
                    "created_by": creator,
                    "completed_at": self.now - timedelta(days=1) if status == "DONE" else None,
                },
            )
            if created:
                self._stamp(task, created_at=self.now - timedelta(days=14 - staff_index))
                record_audit(
                    organization=self.organization,
                    actor=creator,
                    action="task.created",
                    resource=task,
                    after={"title": task.title, "assignee_id": str(task.assignee_id)},
                )

    def _seed_activity(self):
        """Add a spread of historical audit events so the activity feed has depth."""
        actor = self.users["demo@example.com"]
        recruiter = self.users["maya.chen@northstar.example"]
        events = [
            (recruiter, "candidate.created", "candidates.candidate", 96, {"source": "REFERRAL"}),
            (recruiter, "resume.uploaded", "candidates.resume", 94, {"parser_status": "COMPLETED"}),
            (actor, "job.created", "jobs.job", 90, {"title": "Senior Backend Engineer"}),
            (actor, "job.status_changed", "jobs.job", 88, {"status": "OPEN"}),
            (recruiter, "application.created", "applications.application", 78, {"source": "REFERRAL"}),
            (recruiter, "application.stage_moved", "applications.application", 71, {"stage": "Screening"}),
            (actor, "interview.scheduled", "interviews.interview", 64, {"type": "VIDEO"}),
            (actor, "report.exported", "applications.application", 40, {"range": "90", "rows": 36}),
            (actor, "scorecard.reopened", "interviews.scorecardsubmission", 26, {"reason": "Evidence added after the debrief."}),
            (recruiter, "candidate.merged", "candidates.candidate", 18, {"duplicates": 1}),
            (actor, "member.invited", "accounts.invitation", 12, {"role": "INTERVIEWER"}),
            (recruiter, "application.rejected", "applications.application", 6, {"reason": "Skills mismatch"}),
            (actor, "task.created", "tasks.task", 4, {"title": "Draft the Q3 hiring plan"}),
            (actor, "report.exported", "applications.application", 1, {"range": "30", "rows": 12}),
        ]
        for event_actor, action, resource_type, days_ago, after in events:
            log = AuditLog.objects.create(
                organization=self.organization,
                actor=event_actor,
                action=action,
                resource_type=resource_type,
                resource_id=str(self.organization.id),
                after=after,
                ip_address="203.0.113.10",
                user_agent="Mozilla/5.0 (demo seed)",
            )
            self._stamp(log, created_at=self.now - timedelta(days=days_ago))

    # ------------------------------------------------------------------ output

    def _report(self):
        hired = Application.objects.filter(
            organization=self.organization, status=Application.Status.HIRED
        ).count()
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo workspace ready — Northstar Labs"))
        self.stdout.write(f"  Jobs            {len(self.jobs)}")
        self.stdout.write(f"  Candidates      {len(self.candidates)}")
        self.stdout.write(f"  Applications    {len(self.applications)} ({hired} hired)")
        self.stdout.write(f"  Interviews      {Interview.objects.filter(application__organization=self.organization).count()}")
        self.stdout.write(f"  Tasks           {Task.objects.filter(organization=self.organization).count()}")
        self.stdout.write("")
        self.stdout.write(f"  Sign in         {settings.DEMO_EMAIL} / {settings.DEMO_PASSWORD}")
        self.stdout.write("  Django admin    admin@example.com / DemoPass123!")
        self.stdout.write("")


_EVIDENCE = {
    "Role expertise": "Walked through two production systems they owned end to end, including the trade-offs.",
    "Problem solving": "Diagnosed the failure mode methodically and proposed a testable mitigation.",
    "Communication": "Explained a complex migration clearly and checked understanding before moving on.",
    "default": "Gave a concrete example with the outcome and what they would change next time.",
}


def _snapshot(template):
    return {
        "name": template.name,
        "version": template.version,
        "criteria": [
            {
                "id": str(row.id),
                "competency": row.competency,
                "description": row.description,
                "weight": str(row.weight),
                "position": row.position,
            }
            for row in template.criteria.all()
        ],
    }


def _slugify(value):
    from django.utils.text import slugify

    return slugify(value)[:180]
