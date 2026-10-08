# Project Audit — Aster ATS Resume Parser

**Audit date:** 2026-09-12
**Scope:** entire repository (`apps/`, `config/`, `templates_jinja2/`, `assets/`, `scripts/`, deployment files)
**Method:** full source read of every non-generated file, dependency review, PRD-to-implementation trace, runtime verification.

---

## 0. Executive summary

This is **not** a beginner project. It is a genuinely well-architected Django modular monolith with a
real service layer, a real policy/permission layer, tenant isolation, field-level PII encryption,
idempotent mutations, optimistic concurrency, audit logging, and an async resume pipeline. The visual
design is restrained and professional — there is almost no "AI slop" to remove.

The problems are therefore **not** "broken app" problems. They are **completeness, deployment and
product-surface** problems:

| Area | Verdict |
|---|---|
| Backend architecture | Strong — service layer + policies + audit are textbook |
| Backend completeness | **Weak** — read-mostly API, no CRUD endpoints, no reports/offers/tasks/email |
| Frontend quality | Strong — consistent design tokens, no gradients, no emoji icons |
| Frontend completeness | **Weak** — no landing page, no reports, no tasks, no profile, no error pages, no charts |
| Database design | Strong — UUID PKs, tenant-aware uniques, sensible indexes |
| Deployment readiness | **Weak** — no PostgreSQL support, no `.gitignore`, no landing page, committed SQLite DB |
| Feature completeness vs PRD MVP | **~57%** |

**The single most important finding:** the `DATABASE_URL` you supplied is a **PostgreSQL** (Neon)
connection string, but `config/settings.py` only understands `mysql*` URLs. Any Postgres URL is
**silently ignored and the app falls back to local SQLite**. Deploying this as-is against Neon would
silently lose all data on every container restart.

---

## 1. Stack & architecture as found

**Backend**
- Django 5.2 LTS, Python ≥ 3.12 (Docker image uses 3.14-slim)
- Jinja2 backend for application templates + DjangoTemplates for `/ops/` admin
- DRF + drf-spectacular (OpenAPI at `/api/schema/`, Swagger at `/api/docs/`)
- Celery + Redis; eager in DEBUG (`CELERY_TASK_ALWAYS_EAGER`)
- PyMySQL aliased to `MySQLdb` in `config/__init__.py`
- App layout: `accounts, audit, jobs, candidates, applications, interviews, web, api, common`

**Frontend**
- Tailwind CSS 4 via `@tailwindcss/cli`, esbuild bundle to `static_dist/`
- HTMX 2, Alpine (CSP build), SortableJS, Lucide icons, Chart.js, Manrope variable font
- 24 Jinja2 templates; macros in `templates_jinja2/components/ui.html`

**Infrastructure**
- `compose.yaml`: web, worker, beat, MySQL 8.4, Redis 8, MinIO, Mailpit, ClamAV
- `Dockerfile`: multi-stage (node assets → python runtime), gunicorn
- `.github/workflows/ci.yml`: ruff → check → makemigrations --check → migrate → pytest → npm build

**Domain model (implemented)**
`Organization`, `OrganizationMembership`, `Invitation`, `User` · `Department`, `Location`, `Skill`,
`PipelineTemplate(+Stage)`, `Job`, `PipelineStage`, `JobTeamMember`, `JobSkillRequirement`,
`ScorecardTemplate(+Criterion)` · `Candidate`, `Resume`, `ResumeParseResult`, `ResumeExperience`,
`ResumeEducation`, `CandidateSkill`, `DuplicateSuggestion` · `Application`, `ApplicationStageHistory`,
`MatchScore`, `RejectionReason`, `IdempotencyRecord` · `Interview`, `InterviewParticipant`,
`ScorecardSubmission`, `ScorecardAnswer` · `AuditLog`

---

## 2. FRONTEND

### Current status
Server-rendered, progressively enhanced, **no SPA**. This is the right choice for the domain and it is
executed well. A real design system lives in `assets/css/app.css` under `@theme`:

```
canvas #f6f8fb · surface #fff · ink/navy #0f172a/#0b1739
brand-50..950 (cobalt) · slate-50..950
status: emerald (success) · amber (warning) · rose (danger) · sky (info)
shadows: panel, float · font: Manrope Variable
```

Components are consistently tokenised: `.panel`, `.stat-tile`, `.data-table`, `.badge-*`, `.btn-*`,
`.nav-link`, `.kanban-*`, `.auth-*`, `.public-*`. There are **no random gradients, no emoji icons, no
template-dashboard pastiche**. Accessibility basics are present: skip link, `aria-label`s, visible
focus rings, `prefers-reduced-motion`, responsive `data-table--responsive` card fallback at `md`.

### Problems
1. **Dead frontend dependencies.** `chart.js` is in `package.json` but never imported — the app has
   **zero charts**, yet the dashboard is the natural place for them. `htmx.org` is bundled and
   `htmx:afterSwap` / `htmx:responseError` listeners are registered, but **no template uses a single
   `hx-*` attribute**. Alpine is started but **no template uses `x-data`**. Three libraries, three
   sets of bytes shipped, zero behaviour.
2. **No loading states.** Every submit is a full page navigation with no pending affordance; the
   kanban drag is a blocking `fetch` with no spinner.
3. **`window.alert()` for errors** (`assets/js/app.js`) on kanban move failure and HTMX response
   error. Inconsistent with the designed `.alert-*` components.
4. **No pagination anywhere.** Candidates, jobs, audit (hard-capped at 200), interviews all render
   unbounded lists.
5. **Misleading funnel scaling.** `dashboard/index.html` sets bar width `row.total * 12` capped at
   100 — bar lengths do not represent proportions.
6. **No empty/loading distinction.** Empty states exist and are good; loading states do not exist.
7. **No error pages.** No `404.html` / `500.html`. With `DEBUG=False` users get Django's bare
   default; with `DEBUG=True` a stack trace.
8. **No landing page.** `/` → `app_entry` → immediate redirect to login. A portfolio needs a public
   entry surface.
9. **No user-facing notifications UI.** `django.contrib.messages` renders as inline alerts only.
10. **Public careers page is thin.** No meta description / Open Graph, no per-org branding, no job
    count or department filter, no favicon (`href="data:,"`).
11. **Leftover token confusion.** `--color-violet-*` is redefined to the blue brand ramp (a remnant
    of the PRD's violet/indigo choice). Harmless but misleading.
12. **Kanban has no touch affordance** and the drag handle is small; the keyboard fallback is good but
    hidden inside `<details>`.
13. **`static_dist/` is committed** (built artefacts) alongside source — acceptable for Docker, but
    combined with a committed `db.sqlite3` it signals a repo that was not cleaned for publishing.

### Missing parts
Landing page · reports/analytics page with charts · tasks page · personal profile & settings page ·
error pages · pagination · loading/skeleton states · toast notifications · dark mode (optional) ·
notifications centre · CSV/export affordances · candidate create/edit forms · job edit form ·
interview cancel/reschedule UI · scorecard reopen UI.

---

## 3. BACKEND

### Current status
High quality. The separation the PRD asked for is real:

```
Views (thin) → Services (transactional, @transaction.atomic) → Models
                       ↘ policies.py (role + object scoping) → AuditLog
```

Implemented services: `create_job`, `transition_job`, `create_public_application`, `merge_candidates`,
`move_application_stage`, `reject_application`, `recompute_match_score`, `schedule_interview`,
`save_scorecard`, `reopen_scorecard`.

Security posture is genuinely good: UUID PKs, encrypted PII (`Fernet`) + HMAC lookup hashes, tenant
scoping in the **queryset** (not just template hiding), `select_for_update` on stage move / merge /
offer-like flows, `IdempotencyRecord` for public apply and stage move, `version` optimistic
concurrency, CSP + HSTS + nosniff + frame-deny, signed-URL object storage option, magic-byte MIME
sniffing, ClamAV INSTREAM scanning, `Idempotency-Key` requirement, scoped throttling on public apply.

### Problems
1. **`DATABASE_URL` does not support PostgreSQL.** Only `mysql*` is parsed; everything else falls
   through to SQLite. See §0 — this is the highest-severity finding.
2. **API is read-only in practice.** Ten routes exist; only two mutate
   (`move-stage`, `scorecards/submit`). PRD §12.2 requires POST/PATCH on jobs, candidates,
   applications, interviews, plus offers and reports. **No `POST /jobs`, no `PATCH /jobs/{id}`, no
   publish/close endpoints, no candidate create/update/merge, no application create/reject/withdraw,
   no interview create/cancel, no offers, no reports.**
3. **No pagination.** `pagination_class = None` on every list view. PRD requires cursor pagination
   for large collections.
4. **django-filter is configured but unused.** `DEFAULT_FILTER_BACKENDS` is set; no view declares
   `filterset_fields` or `filterset_class`. No API search, filter or ordering.
5. **Response envelope is inconsistent.** Success responses return bare serializer data; only errors
   return `{"error": {...}}`. PRD §6 mandates `{success, message, data}`.
6. **`reopen_scorecard()` is dead code.** The service exists and is correct, but no URL, view or API
   exposes it — so the PRD acceptance criterion *"scorecard cannot be edited after lock without
   admin workflow"* has no workflow. Locked scorecards are permanently frozen.
7. **No spam/abuse protection on the HTML public apply form.** CAPTCHA and spam detection are absent;
   rate limiting exists only on the JSON API path, not the form path.
8. **No login rate limiting** (no `django-axes`/throttle on the login view).
9. **No `SECURE_PROXY_SSL_HEADER`.** With `DEBUG=False`, `SECURE_SSL_REDIRECT=True` behind any
   TLS-terminating proxy can produce a redirect loop.
10. **No `CSRF_TRUSTED_ORIGINS` default** for a deployed host — must be set manually or POSTs fail.
11. **Job creation bypasses the ModelForm's cleaned fields** via `list(form._meta.fields)` — fragile
    coupling to `Meta`; adding a field to the form silently changes the service payload.
12. **`resume.application_set`** relies on the implicit reverse accessor (no `related_name`) — works,
    but undocumented and easy to break.
13. **No `Offers`, `Tasks`, `EmailTemplate`, `EmailMessage` models** — three whole PRD sections
    (§10.7) are unimplemented, and `EmailMessage`/`EmailTemplate` are referenced by PRD §5.5 and §16.
14. **`AuditLog` list is capped at `[:200]`** with no pagination or filter — silently truncates.
15. **`/healthz` does not touch the database** (only `/readyz` does); fine, but no version/build info
    is exposed for deploy verification.
16. **`DATA_UPLOAD_MAX_MEMORY_SIZE` was left at Django's 2.5 MB default** while `MAX_RESUME_MB`
    allows 10 MB, so a legitimate 3–10 MB resume was rejected with an opaque 400 before the app's own
    friendly validation could run. *(fixed during this pass)*
17. **`.gitignore` was missing `static_dist/` and the `.env.*` variants**, so built assets and local
    environment files could be published. *(fixed during this pass)*

**Verified non-issues** (checked because they look like bugs but are correct):
`_create_duplicate_suggestions` already guards `candidate.phone_hash and ...`, so empty phone hashes
do **not** create false duplicates; tenant scoping, idempotency and version checks all behave as
documented under test.

### Missing parts
Job create/update/publish/close API · candidate create/update/merge API · application
create/reject/withdraw API · interview create/update/cancel API · reports API (funnel, time-in-stage,
source quality) · CSV export with audit trail · offers module · tasks module · email templates &
message log · scorecard reopen workflow · pagination · API filters/search/ordering · consistent
response envelope · rate limiting on web forms · landing/error pages · profile management.

---

## 4. DATABASE

### Current status
Well designed for the domain. UUID primary keys everywhere, `organization_id` on every tenant
aggregate, tenant-aware unique constraints (`unique_job_slug`, `unique_department_name`,
`unique_job_candidate`, `unique_interview_user`, …), sensible composite indexes
(`jobs(organization,status,created_at)`, `applications(job,current_stage,status)`,
`candidates(organization,email_hash)`, `interviews(start_at,status)`,
`audit_logs(organization,created_at)`), JSON for flexible config (`explanation`, `template_snapshot`,
`signals`), and encrypted-PII columns separated from their lookup hashes.

Migrations: 8 across 6 apps, all consistent with the models (verified with `makemigrations --check`).

### Problems
1. **Soft delete is partial.** `Candidate.deleted_at` and `Job.deleted_at` exist; `Application`,
   `Resume`, `Interview` have none — PRD §10.1 asks for soft delete on application too.
2. **No retention/anonymisation machinery.** `Organization.retention_days` and
   `Candidate.data_retention_until`-style fields are declared in the PRD but the app only has
   `retention_days` on `Organization`; there is no enforcement task.
3. **`db.sqlite3` (868 KB) sits in the working tree.** It *is* covered by `.gitignore`
   (`*.sqlite3`), so this is a local-hygiene issue rather than a leak — but a reviewer browsing the
   folder will see it, and the `.gitignore` was missing `static_dist/` and `.env.*` variants.
   *(tightened during this pass)*
4. **No `Offers`, `Tasks`, `EmailTemplates`, `EmailMessages` tables** (PRD §10.7).
5. **`DuplicateSuggestion` has no index on `(organization, status)`** despite being queried that way.
6. **`MatchScore` has no `created_at`-based index** for "score recency" reporting (minor).
7. **`FULLTEXT` search** (PRD §10.8) is not implemented; candidate search is `icontains` only, which
   will not scale and does not rank.

### Missing parts
Offers/tasks/email tables · retention enforcement · soft delete on applications · search index ·
duplicate-suggestion index · Postgres-ready configuration and `psycopg` driver.

---

## 5. UI/UX

### Quality score: **7.5 / 10**
Genuinely above the usual bar for a generated project. Consistent spacing scale, restrained palette,
one icon system (Lucide, stroked, no emoji), real typographic hierarchy (`.page-title` 28–32px
`-0.045em` tracking, `.eyebrow` 10px uppercase), accessible focus rings, responsive table-to-card
collapse, reduced-motion support, skip-to-content link, and an editorial auth screen.

### Problems
- No loading states, no toasts, no pagination, no charts (despite the dependency), `window.alert()`
  for errors, misleading funnel bars, no error pages, no landing page, no profile page, no dark mode,
  empty dashboards on a fresh install, no mobile-specific kanban interaction.
- The audit "diff viewer" dumps raw JSON into a dark `<pre>` — functional but the crudest surface in
  the app, and it is the surface a security-minded reviewer will look at first.
- Public careers page has no social/meta polish, which matters because it is the only URL an
  external evaluator can see without credentials.

---

## 6. FEATURE COMPLETENESS

| PRD area | Status |
|---|---|
| Organization / users / roles / permissions | ✅ implemented |
| Departments / locations / skills | ⚠️ model + admin only, **no app UI** |
| Jobs (create, transition, publish, careers page) | ✅ create + transition; ❌ **no edit UI** |
| Candidates + resume upload + async parse | ✅ |
| Candidate deduplication + merge | ✅ |
| Applications + stage history + kanban | ✅ |
| Interviews + scorecard + lock | ✅ schedule + submit + lock; ❌ no cancel, ❌ **no reopen workflow** |
| Rejection reasons | ✅ |
| Reports (funnel, time-in-stage, source quality) | ❌ **not implemented** |
| Tasks | ❌ **not implemented** |
| Email templates / message log / composer | ❌ **not implemented** |
| Offers + approval | ❌ **not implemented** |
| Exports (CSV) + audit of export | ❌ **not implemented** |
| Notifications | ❌ in-app only via messages |
| Search / filter / sorting / pagination | ⚠️ basic `icontains` search on 3 lists, no pagination |
| REST API (full CRUD) | ⚠️ read-mostly, 2 mutations |
| Landing page / error pages | ❌ |
| Demo account + demo data | ⚠️ `seed_demo` exists (`admin@example.com`), no `demo@` account, single job |
| README / `.env.example` / deploy | ⚠️ exist but not portfolio-grade; **no Postgres** |

### **Feature completeness: ~57%** of the PRD MVP.

---

## 7. Severity-ranked issue list

| # | Sev | Issue | Location |
|---|---|---|---|
| 1 | **Critical** | PostgreSQL/Neon `DATABASE_URL` unsupported → silent SQLite fallback | `config/settings.py:83` |
| 2 | **Critical** | Upload limit 2.5 MB vs advertised 10 MB → valid resumes rejected | `config/settings.py` |
| 3 | **High** | Locked scorecards cannot be reopened (service unreachable) | `apps/interviews/services.py:133` |
| 4 | **High** | Locked scorecards cannot be reopened (service unreachable) | `apps/interviews/services.py:133` |
| 5 | **High** | No API CRUD for jobs/candidates/applications/interviews | `apps/api/` |
| 6 | **High** | No reports / analytics / export | — |
| 7 | **High** | No landing page, no 404/500 pages | `templates_jinja2/` |
| 8 | **High** | No pagination anywhere (incl. audit `[:200]`) | views |
| 8 | **High** | No candidate create/edit, no job edit UI | `apps/web/views.py` |
| 9 | **Medium** | No loading states / toasts; `window.alert()` for errors | `assets/js/app.js` |
| 10 | **Medium** | Dead dependencies (Chart.js unused, HTMX/Alpine unused) | `package.json`, templates |
| 11 | **Medium** | Inconsistent API envelope; django-filter configured but unused | `apps/api/` |
| 12 | **Medium** | No rate limiting on HTML apply / login | `apps/web/views.py`, `apps/accounts/views.py` |
| 13 | **Medium** | No `SECURE_PROXY_SSL_HEADER` → redirect-loop risk | `config/settings.py` |
| 14 | **Medium** | Misleading funnel bar scaling | `templates_jinja2/dashboard/index.html` |
| 15 | **Medium** | No static serving in production (gunicorn only, no WhiteNoise) | `Dockerfile`, settings |
| 16 | **Low** | Partial soft delete; no retention enforcement | models |
| 17 | **Low** | `form._meta.fields` coupling | `apps/web/views.py:208` |
| 18 | **Low** | `violet-*` tokens redefined to blue | `assets/css/app.css` |
| 19 | **Low** | No meta/OG tags or favicon | templates |
| 20 | **Low** | Public apply has no CAPTCHA/spam guard | `apps/web/views.py` |

---

## 8. Remediation plan (execution order)

**Phase 1 — Correctness & deployment (critical/high)**
1. Add PostgreSQL support to `settings.py` (+ `psycopg[binary]`), with `CONN_MAX_AGE`, TLS and
   platform host detection for Neon; make `DATABASE_URL` fail loudly on unknown schemes.
2. Raise `DATA_UPLOAD_MAX_MEMORY_SIZE` to match `MAX_RESUME_MB`; add WhiteNoise so gunicorn can
   serve static assets without a separate web server; add `SECURE_PROXY_SSL_HEADER`.
3. Tighten `.gitignore`; expose the scorecard reopen workflow (view + API + UI).
4. Add 404/500 templates; add a real public landing page.

**Phase 2 — Completeness**
6. Reports page + CSV export (funnel, time-in-stage, time-to-hire, source quality) with audit trail.
7. Tasks module (model, service, page).
8. Candidate create/edit, job edit, interview cancel.
9. Profile/settings page.
10. Pagination on all lists; loading + toast states.

**Phase 3 — API & product polish**
11. Full CRUD API with a consistent `{success, message, data}` envelope.
12. Pagination, search, filter, ordering on API collections.
13. Rate limiting on web apply and login.
14. Dashboard charts (finally use Chart.js) and honest funnel scaling.
15. Meta/OG tags, favicon, per-org careers branding.

**Phase 4 — Portfolio readiness**
16. `demo@example.com` / `demo123` plus rich, realistic demo data (multiple jobs, candidates,
    interviews, scorecards, tasks, activity) so no surface is ever empty.
17. Rewrite `README.md` for a public audience; refresh `.env.example`.
18. Re-run ruff / check / makemigrations --check / pytest / npm build; publish the final scorecard.

---

*See `QUALITY_REVIEW.md` for the post-implementation scorecard and remaining issues.*
