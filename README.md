# Aster ATS — Applicant Tracking System

A multi-tenant applicant tracking system for engineering teams: requisitions, careers pages,
secure resume intake, deterministic parsing, explainable matching, an audited hiring pipeline,
interviews with locked scorecards, tasks, and hiring analytics.

Built with Django 5.2 LTS, server-rendered Jinja2 templates, HTMX + Alpine for interaction, and a
DRF API. Every screen is backed by real data — the demo workspace ships with six requisitions,
34 candidates, 36 applications, completed interviews with submitted scorecards, and a task board.

**Live demo:** [https://ats.example.com](https://ats.example.com) · **API docs:** `/api/docs/`

---

## Table of contents

- [Screenshots](#screenshots)
- [Feature overview](#feature-overview)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Environment variables](#environment-variables)
- [Demo account](#demo-account)
- [Using the API](#using-the-api)
- [Testing and quality gates](#testing-and-quality-gates)
- [Deployment](#deployment)
- [Security model](#security-model)
- [Project layout](#project-layout)

---

## Screenshots

| Landing page | Hiring dashboard |
| --- | --- |
| ![Landing page](docs/screenshots/landing.png) | ![Dashboard](docs/screenshots/dashboard.png) |

| Kanban pipeline | Reports and analytics |
| --- | --- |
| ![Pipeline](docs/screenshots/pipeline.png) | ![Reports](docs/screenshots/reports.png) |

| Candidate profile | Mobile |
| --- | --- |
| ![Candidate](docs/screenshots/candidate-detail.png) | ![Mobile](docs/screenshots/mobile-dashboard.png) |

<sub>All screenshots are generated from the seeded demo workspace by
[`scripts/screenshots.mjs`](scripts/screenshots.mjs).</sub>

---

## Feature overview

### Hiring pipeline
- **Requisitions** with department, location, headcount, employment type, experience and education
  requirements, an approval workflow (draft → pending approval → open → paused → closed → archived),
  and optimistic-concurrency versioning.
- **Reusable pipeline templates** that materialise into per-job stages with SLA targets, so a job
  never changes shape under a live pipeline.
- **Public careers pages** per organization, with an open-roles list, job detail, and a throttled
  application endpoint.

### Candidates and resumes
- **Secure intake** for PDF and DOCX: magic-byte MIME sniffing, size limits, SHA-256 checksums, and
  an optional ClamAV INSTREAM scan before anything is stored.
- **Asynchronous parsing** through Celery: scan → text extraction → deterministic field/skill
  extraction → duplicate detection → match scoring. Failures are surfaced as
  `REVIEW_REQUIRED` / `FAILED` rather than silently dropped.
- **Field-level PII encryption** (Fernet) for candidate email and phone, with separate HMAC lookup
  hashes so search and deduplication never decrypt anything.
- **Duplicate review** with scored suggestions and a transactional merge that preserves
  applications, resumes and skills.

### Pipeline and interviews
- **Kanban board** with drag-and-drop movement, keyboard-accessible fallbacks, and server-side
  stage rules (a stage that requires an interview or scorecard cannot be entered without one).
- **Idempotent mutations** — every stage move carries an `Idempotency-Key`, so a retried request
  cannot move a candidate twice.
- **Interviews** with participant lists, scorecard template snapshots, and invite dispatch.
- **Scorecards** that require evidence for extreme ratings (1 or 5), lock on submission, and can
  only be reopened by an HR or organization administrator with a recorded reason.

### Tasks and analytics
- **Task board** with assignee, priority, due dates, and overdue tracking.
- **Reports**: hiring funnel with step conversion, average time in stage, time to hire (average and
  median), source quality with hire rate, interviewer completion, and aging candidates against SLA.
- **Audited CSV export** that records the range and row count, and only includes candidate contact
  details for roles allowed to see them.

### Platform
- **Tenant isolation** enforced in the queryset layer, never in the template.
- **Six roles** (organization admin, HR admin, recruiter, hiring manager, interviewer, auditor) with
  object-level scoping — a hiring manager sees only their own requisitions, an interviewer only the
  candidates they are scheduled with.
- **Append-only audit log** for every state change, with before/after diffs, actor, IP and request ID.
- **Demo mode** with a seeded workspace and a one-click sign-in hint on the landing page.

---

## Tech stack

| Layer | Choice |
| --- | --- |
| Framework | Django 5.2 LTS (Python 3.12+) |
| Templates | Jinja2 for the app, DjangoTemplates for `/ops/` admin |
| Interactivity | HTMX 2, Alpine.js (CSP build), SortableJS |
| Styling | Tailwind CSS 4 with `@theme` design tokens |
| Charts | Chart.js 4 |
| Icons | Lucide (single icon system, no mixed sets) |
| API | Django REST Framework + drf-spectacular (OpenAPI 3) |
| Database | PostgreSQL (production) · MySQL 8 (compose) · SQLite (local) |
| Async | Celery + Redis |
| Static files | WhiteNoise + `CompressedManifestStaticFilesStorage` |
| Assets | esbuild (JS) + Tailwind CLI (CSS) |
| Testing | pytest + pytest-django + pytest-cov, ruff |

---

## Architecture

A modular monolith with a strict one-way dependency between layers:

```
Request ──▶ urls.py ──▶ views.py ──▶ services.py ──▶ models.py
   (routing)   (thin)    (rules + transactions)   (data)
```

- **Views** parse input, resolve the tenant and membership, and render. They contain no business
  rules.
- **Services** own every state change. They are wrapped in `@transaction.atomic`, call
  `require_permission(...)`, enforce invariants, and write to the audit log. The web UI and the REST
  API call the *same* service functions, so the two surfaces cannot drift apart.
- **Policies** (`apps/accounts/policies.py`) centralise the role matrix and queryset scoping. Every
  list view starts from a `scope_*(membership, queryset)` call.

```
apps/
├── accounts/     users, organizations, memberships, invitations, roles, MFA, policies
├── jobs/         departments, locations, skills, requisitions, pipeline & scorecard templates
├── candidates/   candidate records, encrypted PII, resumes, parsing, matching, dedupe
├── applications/ applications, stage history, match scores, idempotency records
├── interviews/   interviews, participants, scorecard submissions and answers
├── tasks/        recruiter follow-ups
├── reports/      tenant-scoped hiring analytics and CSV export
├── audit/        append-only audit log
├── api/          DRF serializers, views and the response envelope
├── web/          the server-rendered application
└── common/       crypto, storage, pagination, middleware, logging, base models
```

### API response envelope

Every response uses one shape so clients can branch on a single key:

```json
{ "success": true,  "message": "Job draft created.", "data": { "...": "..." } }
{ "success": false, "error": { "code": "validation_error", "message": "...", "fields": {}, "request_id": "..." } }
```

List endpoints are paginated (`?page=`, `?page_size=`, max 100).

---

## Getting started

### Prerequisites

- Python 3.12 or newer
- Node.js 20 or newer (for the asset build)
- Optional: Docker Desktop for the full stack (MySQL, Redis, MinIO, Mailpit, ClamAV)

### Local development (SQLite, no Docker)

```bash
git clone <your-repo-url> ats-resume-parser
cd ats-resume-parser

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

npm install
npm run build                      # Tailwind CSS + esbuild bundle + fonts

cp .env.example .env               # optional: SQLite is the default with no .env at all

python manage.py migrate
python manage.py seed_demo         # creates the demo workspace and accounts
python manage.py runserver
```

Open <http://127.0.0.1:8000>. With no `.env`, the project uses SQLite, eager Celery tasks, local
file storage and the console email backend, so nothing else needs to be running.

> `seed_demo` is idempotent. Re-run it any time, or pass `--reset` to delete and rebuild the demo
> workspace from scratch.

### Full service stack (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
| --- | --- |
| Application | <http://127.0.0.1:8000> |
| API documentation | <http://127.0.0.1:8000/api/docs/> |
| Mailpit (captured email) | <http://127.0.0.1:8025> |
| MinIO console | <http://127.0.0.1:9001> |
| Health / readiness | `/healthz` · `/readyz` |
| Operations admin | `/ops/` |

### Using a hosted PostgreSQL (Neon, Supabase, RDS)

Set a single connection string and nothing else changes:

```bash
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

The settings module detects the scheme, applies `sslmode=require` for Postgres, and enables
connection reuse (`CONN_MAX_AGE=600`) outside DEBUG. `postgres://`, `postgresql://`, `mysql://`
and `sqlite://` are all supported; an unknown scheme fails fast with a clear error.

---

## Environment variables

The complete list with inline documentation lives in [`.env.example`](.env.example). The ones that
matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `unsafe-development-key` | **Required in production.** Session and signing key. |
| `DJANGO_DEBUG` | `true` | Must be `false` in production. Enables HSTS, secure cookies, SSL redirect. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma separated. Platform hostnames are auto-detected on Render/Railway/Fly. |
| `DATABASE_URL` | *(empty → SQLite)* | `postgresql://…`, `mysql://…` or `sqlite://…`. |
| `DB_CONN_MAX_AGE` | `0` in DEBUG, `600` otherwise | Persistent connection lifetime in seconds. |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker for Celery. Tasks run inline when DEBUG and eager. |
| `CELERY_TASK_ALWAYS_EAGER` | `true` in DEBUG | Run the resume pipeline in-process instead of on a worker. |
| `USE_S3` | `false` | Switch resume storage to S3/MinIO. |
| `FIELD_ENCRYPTION_KEY` | *(derived from SECRET_KEY)* | Fernet key for candidate PII. **Set explicitly in production.** |
| `PII_HASH_KEY` | `DJANGO_SECRET_KEY` | Pepper for contact lookup hashes. Use a distinct value. |
| `MAX_RESUME_MB` | `10` | Upload ceiling; also raises Django's request body limit to match. |
| `CLAMAV_HOST` / `MALWARE_SCAN_REQUIRED` | *(empty)* / `false` | Virus scanning for uploads. |
| `MFA_ENFORCEMENT` | `false` | Force TOTP enrolment for admins and staff. |
| `SECURE_SSL_REDIRECT` | `true` when `DEBUG=false` | Redirect HTTP to HTTPS behind a proxy. |
| `DEMO_EMAIL` / `DEMO_PASSWORD` | `demo@example.com` / `demo123` | Credentials advertised on the landing page when the user exists. |

---

## Demo account

`seed_demo` builds a complete workspace for **Northstar Labs** and two sign-in options:

| Account | Password | Role | Use |
| --- | --- | --- | --- |
| `demo@example.com` | `demo123` | Organization admin | The portfolio demo. Full access to every screen. |
| `admin@example.com` | `DemoPass123!` | Superuser | Django admin at `/ops/` only. Kept separate so the public demo cannot reach it. |

Other members are seeded to exercise role scoping: an HR admin, a recruiter, a hiring manager, two
interviewers, and an auditor — all with the demo password. Sign in as the interviewer to see how
the candidate list narrows to only the people they are scheduled with.

The seeded workspace contains 6 requisitions (4 open, 1 paused, 1 closed), 34 candidates across
five countries, 36 applications spread across every pipeline stage, 18 interviews, submitted
scorecards, 12 tasks, and a full audit trail.

---

## Using the API

Interactive documentation is served at `/api/docs/` (Swagger UI) with the raw schema at
`/api/schema/`. Authentication uses the session cookie, so the fastest way to explore is to sign in
through the UI and then open `/api/docs/`.

```bash
# Public careers API — no authentication
curl "http://127.0.0.1:8000/api/v1/public/organizations/northstar-labs/jobs"

# Organization-scoped, session authenticated
curl -b cookies.txt "http://127.0.0.1:8000/api/v1/organizations/northstar-labs/dashboard"

# Analytics for a specific window
curl -b cookies.txt "http://127.0.0.1:8000/api/v1/organizations/northstar-labs/reports?range=90"
```

### Endpoint map

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/public/organizations/{org}/jobs` | Open roles only. Anonymous, paginated. |
| `GET` | `/api/v1/public/organizations/{org}/jobs/{slug}` | Job detail. 404 unless open and live. |
| `POST` | `/api/v1/public/organizations/{org}/jobs/{slug}/apply` | Requires `Idempotency-Key`. Throttled. |
| `GET` `POST` | `/api/v1/organizations/{org}/jobs` | List / create a draft. `?status=`, `?q=`. |
| `GET` `PATCH` | `/api/v1/organizations/{org}/jobs/{id}` | Retrieve / edit. Bumps `version`. |
| `POST` | `/api/v1/organizations/{org}/jobs/{id}/transition` | Publish, pause, close. Enforces the approval graph. |
| `GET` `POST` | `/api/v1/organizations/{org}/candidates` | List / create. `?q=` searches name and title. |
| `GET` `PATCH` | `/api/v1/organizations/{org}/candidates/{id}` | Retrieve / edit. Re-encrypts contact details. |
| `GET` | `/api/v1/organizations/{org}/resumes` | `?parser_status=` filter. |
| `GET` | `/api/v1/organizations/{org}/applications` | `?job=`, `?status=`, `?stage_type=`. |
| `POST` | `/api/v1/organizations/{org}/applications/{id}/move-stage` | Requires `Idempotency-Key` and `expected_version`. |
| `POST` | `/api/v1/organizations/{org}/applications/{id}/reject` | Tenant-checked rejection reason. |
| `GET` `POST` | `/api/v1/organizations/{org}/interviews` | List / schedule with participants. |
| `GET` | `/api/v1/organizations/{org}/interviews/{id}` | Retrieve. |
| `POST` | `/api/v1/organizations/{org}/interviews/{id}/cancel` | Idempotent, audited. |
| `POST` | `/api/v1/organizations/{org}/interviews/{id}/complete` | Idempotent, audited. |
| `POST` | `/api/v1/organizations/{org}/interviews/{id}/scorecards/submit` | Evidence required for ratings 1 and 5. |
| `GET` `POST` | `/api/v1/organizations/{org}/tasks` | List / create. `?status=open`, `?assignee=me`. |
| `GET` `PATCH` | `/api/v1/organizations/{org}/tasks/{id}` | Retrieve / patch, including status. |
| `GET` | `/api/v1/organizations/{org}/reports` | `?range=30\|90\|365\|all`. |
| `GET` | `/api/v1/organizations/{org}/dashboard` | The same numbers the dashboard renders. |

---

## Testing and quality gates

```bash
ruff check .                                  # lint
python manage.py check                        # Django system checks
python manage.py makemigrations --check --dry-run   # no missing migrations
pytest -W error                               # test suite, warnings treated as failures
pytest --cov --cov-fail-under=80              # coverage floor (CI enforces 80%)
npm run build                                 # asset build must succeed
```

**192 tests, 96% statement coverage** across `apps/` and `config/` (excluding migrations); 59 of
84 modules sit at 100%. CI runs the suite with `-W error`, so a stray `DeprecationWarning` or an
unclosed file fails the build rather than scrolling past.

The suite covers tenant isolation, role scoping across both the web UI and the API, idempotent and
versioned stage movement, explainable match scoring, scorecard evidence rules and locking,
public-application deduplication, async DOCX parsing, the API response envelope and pagination,
audited task and interview transitions, interview invitations (including parsing the generated
`.ics` back), seeder idempotency, sign-in with and enrolment of a TOTP authenticator, invitation
acceptance (including the account-takeover refusal), and a walk of **every route in the product**
(41 URLs) asserting each one renders a non-empty body and never returns a 5xx.

It also **exercises the write paths**, not just the rendering: publishing a job, moving an
application through the pipeline, rejecting a candidate, cancelling and completing interviews,
submitting and reopening scorecards, updating a profile, and inviting a teammate are all posted
end to end. Three real defects were found by doing this rather than by reading the code — a task
closed through the API that kept `completed_at = null`, a stale scorecard link that returned a
500, and a malformed version field that crashed the publish endpoint. All three are fixed and
pinned by regression tests.

The web **form POST handlers** are likewise exercised: `job_create`, `job_edit` (which runs the
description through bleach — a stored-XSS attempt is stripped on save), `candidate_create` /
`candidate_edit` (encrypted contact storage, email normalisation), `resume_download` (correct
`Content-Disposition` and audit row), `duplicate_merge` (both refusal branches), `interview_create`
(schedules and assigns panellists), `scorecard` (submit locks the scorecard; a non-panellist gets a
404), `scorecard_reopen` (unlocks a submitted scorecard), `profile` password change (verified by
re-login), the `move_stage` 409 when a stage requires an interview, and the `reject` error branch
for an already-rejected application. The task list filters (`?status=open` vs an explicit status,
`?assignee=me` vs a teammate id, `?q=` across title and description) and both task write endpoints
are exercised too. `apps/web/views.py` sits at **98%** and `apps/tasks/views.py` at **97%** as a
result — the interactive surface is genuinely click-tested, not just rendered.

The two highest-consequence subsystems are covered at 100%: **tenant isolation** (`policies.py` —
hiring-manager ownership, recruiter department/location scope, and the rule that an empty scope
means "everything" rather than "nothing") and **resume ingestion** (quarantine, skill linkage,
failure handling, duplicate detection). The ClamAV client is verified against a loopback server
that actually speaks the INSTREAM protocol.

### Notable invariants the tests lock in

- A hiring manager cannot see another manager's requisition; an interviewer sees only the candidates
  they are scheduled with — verified on both the HTML pages and the API.
- Moving an application twice with the same `Idempotency-Key` is a no-op; a stale
  `expected_version` is rejected with a message telling the user to refresh.
- A stage that requires an interview or a submitted scorecard cannot be entered without one.
- A task in `DONE` always carries a `completed_at`, whether it was closed by a transition or
  created that way through the API, and reopening it clears the timestamp again.
- Extreme scorecard ratings require written evidence, and submitted scorecards are locked.
- A missing, stale or cross-tenant scorecard submission is a validation message, never a 500 —
  the same for a malformed job-transition payload.
- The dashboard and the reports page are computed from one shared service, so their funnels cannot
  disagree.

---

## Deployment

### Docker

The `Dockerfile` is a two-stage build: Node compiles the assets, then a slim Python image installs
the package, copies the bundle and runs `collectstatic` at build time. It serves with gunicorn:

```bash
docker build -t aster-ats .
docker run --rm -p 8000:8000 \
  -e DJANGO_DEBUG=false \
  -e DJANGO_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(64))')" \
  -e DATABASE_URL="postgresql://user:pass@host/db?sslmode=require" \
  -e FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  -e PII_HASH_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  aster-ats
```

### Platform checklist

1. Set the environment variables from the table above. `DJANGO_DEBUG=false` is what enables HSTS,
   secure cookies and the HTTPS redirect.
2. Provide `DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and `PII_HASH_KEY`. Keep the Fernet key
   stable — rotating it makes existing encrypted contact details unreadable.
3. Run `python manage.py migrate` as a release command.
4. `collectstatic` runs in the image build; WhiteNoise serves the hashed files, so no CDN is required.
5. Run `python manage.py seed_demo` once if you want the demo workspace on the deployed instance.
6. Point your platform's health check at `/healthz` (liveness) and `/readyz` (checks the database).
7. Add your domain to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. Render, Railway and Fly hostnames
   are detected automatically.

---

## Security model

| Concern | Approach |
| --- | --- |
| Tenant isolation | Every queryset starts from `scope_*(membership, ...)`; the organization comes from the URL and is validated against an active membership. |
| Authorization | A single role → permission matrix in `apps/accounts/policies.py`, plus object-level scoping for hiring managers, recruiters and interviewers. |
| PII at rest | Candidate email and phone are Fernet-encrypted per field; lookups use a separate HMAC hash so search never decrypts. |
| Uploads | Magic-byte MIME sniffing, extension allow-list, SHA-256 checksums, optional ClamAV INSTREAM scan, size ceiling enforced at both the app and Django request level. |
| Concurrency | Optimistic `version` fields on jobs and applications; `select_for_update()` in services; `IdempotencyRecord` for retryable mutations. |
| Transport | HSTS (1 year, subdomains), `SECURE_SSL_REDIRECT`, secure cookies, `X_Frame_Options: DENY`, `nosniff`, `same-origin` referrer policy, `SECURE_PROXY_SSL_HEADER` for proxy-terminated TLS. |
| Abuse | Scoped throttling on the public apply endpoint; generic responses that do not reveal whether a candidate exists. |
| Accountability | Append-only audit log with actor, action, before/after diff, IP, user agent and request ID. |

---

## Project layout

```
config/                 settings, urls, celery, jinja2 environment
apps/                   the Django applications (see Architecture)
assets/                 Tailwind entry point, app.js, favicon
templates_jinja2/       layouts, components, and one folder per feature
static_dist/            build output (CSS, JS, fonts) — generated, not edited
scripts/                asset build helpers
tests/                  pytest suite
docs/screenshots/       images used in this README
```

---

## License

Released for portfolio and evaluation purposes.
