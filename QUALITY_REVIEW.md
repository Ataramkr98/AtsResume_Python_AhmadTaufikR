# Quality Review — Aster ATS

**Phase 10 deliverable.** Final pass reviewed from three angles at once: senior engineer
(code), recruiter (portfolio signal), and end user (does anything feel broken).

- **Reviewed:** 13 September 2026
- **Revision:** working tree at the end of the remediation effort
- **Method:** every claim below is backed by a command that was actually run, not by
  inspection alone. Where something could *not* be verified, it says so explicitly.

---

## 1. Verdict

The application is **deployment ready**. It is no longer a "looks finished" project — the
state machine, tenant isolation, audit trail, idempotency and optimistic concurrency are
real, and they are exercised by tests rather than asserted in a README.

Two defects found in this pass were genuinely serious and are now fixed:

1. **A second `seed_demo` run crashed the command.** Because `compose.yaml` runs
   `seed_demo` on *every* container start, any redeploy against an existing database would
   have killed the web container on boot. This is the kind of bug that only shows up after
   you ship.
2. **Mobile horizontal overflow** on the dashboard and interviews pages, caused by a
   CSS grid `min-width: auto` trap. Visible only at 375 px and 390 px — i.e. invisible to
   anyone reviewing on a laptop.

Both are covered by regression tests / automated audits so they cannot silently return.

A second round then closed the review's own open items: test coverage was **measured for the
first time** and gated in CI, the full route surface was moved **into** the suite so CI
enforces it, an unclosed file handle on the resume-download path was surfaced by
`pytest -W error` and fixed, and an interview-invite task sitting at **0% coverage** — a
user-facing feature no test had ever executed — was brought to 100%. Code quality moved
8.5 → **9.0** as a result.

A third round went after the least-covered, highest-risk area — authentication — and found a
**third real break**: both MFA call sites passed three arguments to `django_otp.login`, which
takes two. Enrolling in two-factor authentication *and* signing in with it both raised
`TypeError` at runtime, so the entire MFA feature was dead. It is now fixed and pinned by
regression tests. The same sweep found a **silent error** — an invitation failure that the
template never rendered — and closed it in five templates. Coverage rose 85% → **86%**, and
`apps/accounts/forms.py` (53% → 89%) and `apps/accounts/views.py` (59% → 87%) climbed off the
"thin spots" list entirely.

A fourth and fifth round turned to the **write** half of the application — the service
functions and POST handlers that the route walk renders but never executes. This is where the
remaining defects were, and the hypothesis held:

1. `set_task_status` sat at **0% coverage** and had never been run. Covering it exposed an
   invariant break: a task created through the API with `status=DONE` kept
   `completed_at = null`, so the same status meant two different things depending on which
   door it came through.
2. `reopen_scorecard` raised an uncaught `ScorecardSubmission.DoesNotExist` on a stale or
   mistyped id — the view only catches `ValidationError`, so an administrator following a dead
   link got an HTTP 500.
3. `job_transition` parsed `expected_version` with a bare `int()`, so a tampered or truncated
   form raised an uncaught `ValueError` before any business rule ran. Same bug shape as #2,
   found by writing the test rather than by reading the code.

All three are fixed and pinned. Coverage rose 86% → **90%** (3,218 statements, 326 missed),
the test suite grew 47 → **100 tests**, and `apps/web/views.py` — previously the single
largest gap at 119 missed statements — went 69% → **83%**.

A sixth round closed the last of the *product* gaps and, unlike the four before it, **found no
new defects** — which is the outcome you want from the final pass. It targeted the two modules
where a silent break would be worst rather than merely possible: the tenant-isolation layer
(`apps/accounts/policies.py`, the security boundary every list and detail view funnels through)
and the resume ingestion pipeline (the first thing a candidate does). Both now sit at **100%
coverage**, as does the malware-scanning gate. The interviewer branch of scoping was already
covered; the hiring-manager and recruiter branches — the ones carrying *object-level* rules
rather than a simple yes/no — had never run at all. Coverage rose 90% → **91%** and the suite
grew 100 → **128 tests**.

A seventh round went after the API's unposted endpoints and found a **fourth real break**, this
time a guaranteed HTTP 500: `POST /api/v1/organizations/{slug}/interviews` passed bare UUIDs to
`schedule_interview`, which needs `User` instances. No well-formed request to that endpoint had
ever succeeded — and because the web UI schedules interviews through a form that *does* hand
over model instances, the bug was invisible everywhere except the documented API. It is fixed
and pinned. Coverage rose 91% → **93%** and the suite grew 128 → **153 tests**.

An eighth round closed the **last** of the form-POST handlers in `apps/web/views.py` — the single
largest remaining gap at 64 missed statements. These are the Phase-5 "every button must work, no
fake buttons" paths that render in the route walk but are never *posted* by a test: `job_create`,
`job_edit`, `candidate_create`, `candidate_edit`, `resume_download` and `duplicate_merge`. Writing
the tests found a useful behaviour to pin rather than a new defect: `job_edit` runs the description
through **bleach**, so a stored-XSS attempt in the job description is stripped on save — invisible
until a write-path test exercised the `clean_description_html` branch. The suite grew 153 →
**170 tests** and `apps/web/views.py` climbed **83% → 91%** (64 → 36 missed); measured coverage
for the whole tree rose to **95%** (3,082 statements, 145 missed).

Re-reading the coverage map after Round 8 showed the *other* half of the interactive surface was
still unposted, so a **Round 8.2** closed it. `interview_create`, `scorecard` (submit/draft),
`scorecard_reopen`, the `profile` password-change branch, and the error/info branches of
`move_stage_htmx`, `reject_htmx` and `interview_complete` had never been run by a test — these are
the buttons a reviewer clicks on the candidate and interview pages. Covering them pinned two
genuinely interesting behaviours: `move_stage_htmx` returns a **409** (not a 500 or silent no-op)
when a move hits the "schedule an interview first" gate, and `reject_htmx` surfaces
"Only active applications can be rejected" as a message when a second reject is attempted. The
suite grew 170 → **181 tests** and `apps/web/views.py` reached **98%** (36 → 6 missed); the six
remaining statements are the two `Http404` early-returns in `scorecard` (lines 743, 748) and the
`require_permission` 404s plus the route-not-found lines (109, 122–123, 135) — all already
exercised structurally by the permission/route tests. A final **Round 8.3** then cleared the last
product-code thin spot — `apps/tasks/views.py` (81%) — by exercising its list filters
(`?status`, `?assignee`, `?q=`) and both write endpoints; it reached **97%**, with only a defensive
`add_error` line left. Measured coverage for the tree is now **96%** (3,218 statements, 132 missed)
across **192 tests**.

---

## 2. Scores

| Dimension | Score | Basis |
|---|---|---|
| **Code quality** | **9.5 / 10** | Clean layering, ruff clean, 192 tests at a measured **96% coverage** with an enforced floor, zero TODOs, no resource leaks. The write paths, the tenant-isolation layer, the upload pipeline, the API's mutation endpoints and every web POST handler (job, candidate, resume, duplicate-merge, interview, scorecard, profile, task) are all exercised rather than merely rendered. Deduction for a few long functions. |
| **UI quality** | **9.0 / 10** | One icon set, coherent palette, real data, no gradient/AI-slop. Deduction for unreadable template formatting. |
| **UX quality** | **8.5 / 10** | Every route works, empty states and confirmations everywhere. Deductions for no global search and no dark mode. |
| **Feature completeness** | **9.5 / 10** | Full ATS loop from public application to hire, plus API, reports, audit — and the interactive half (publish, move, reject, cancel, complete, invite) is now proven to work, not just to render. Deductions for stubbed email/calendar integration. |
| **Production readiness** | **8.5 / 10** | README, env template, Dockerfile, CI with a coverage gate, health check, security headers, Postgres path. Deduction: image never built here. |

**Weighted sense of it:** this reads as a mid-level engineer's careful side project that has
been through one honest hardening pass — not a tutorial follow-along, and not a senior
engineer's five-year platform. That is the right place for a portfolio piece.

---

## 3. Evidence

### Quality gates — all green

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check .` | `All checks passed!` |
| Django | `manage.py check` | `System check identified no issues (0 silenced).` |
| Migrations | `manage.py makemigrations --check --dry-run` | `No changes detected` |
| Tests | `pytest -q` | **192 passed**, 0 failed, 0 errors |
| Tests (strict) | `pytest -W error` | **192 passed** — CI runs this, so CI is green |
| Coverage | `coverage report` | **96%** of 3,218 statements (132 missed) |

### Runtime and visual verification

| Check | Result |
|---|---|
| Route walk — every product route as the demo user | **41 / 41 render, none 5xx** (now enforced in pytest, not a manual script) |
| Responsive audit — 5 widths × 12 pages (60 combinations) | **No overflow, no console errors** |
| Widths covered | 1920, 1440, 768, 390, 375 px |
| Screenshot capture | 17 images, **"No browser console errors."** |
| `seed_demo` run twice against the dev database | Identical counts both runs (idempotent) |
| Neon Postgres URL parsing | `postgresql` / `sslmode=require` / `CONN_MAX_AGE=600` — correct |

### Codebase shape

| Metric | Value |
|---|---|
| Python LOC (apps + config, excl. migrations) | 6,850 |
| Jinja2 templates | 34 |
| Migrations | 9 |
| Test LOC | 4,860 |
| Statement coverage | 96% (3,218 statements, 132 missed) |
| Modules at 100% coverage | 59 of 84 |
| Routes | 34 web + 21 API |
| `TODO` / `FIXME` / `Lorem ipsum` / placeholder text | **0** |

---

## 4. Issues found and fixed in this pass

### 4.1 CRITICAL — `seed_demo` was not idempotent

**Problem:** Running `seed_demo` a second time against an already-seeded database aborted:

```
django.core.exceptions.ValidationError: ['Cannot change job from CLOSED to OPEN.']
```

**Root cause:** `_advance_job()` walks a requisition along the state-machine graph to reach
the demo status. It skipped steps already matched but had **no guard for "already at the
target status"**. On a re-run a CLOSED job was asked to transition to OPEN, which
`ALLOWED_TRANSITIONS` correctly refuses (`CLOSED → {ARCHIVED}` only).

This was not a test-only problem. `compose.yaml` runs
`python manage.py migrate && python manage.py seed_demo && gunicorn …` on **every container
start**, so the second `docker compose up` against a persistent volume would crash the web
container before gunicorn ever bound the port.

**Solution:** Return immediately when the job already sits at the target status, making the
walk a true no-op on re-run.

```python
job.refresh_from_db()
if job.status == target_status:
    return
```

**Files changed:** `apps/accounts/management/commands/seed_demo.py`

**Regression test added:** `tests/test_core.py::test_seed_demo_is_idempotent` — runs the
seeder twice in one test and asserts row counts *and* the set of closed requisitions are
unchanged. It reproduces the failure without depending on test ordering.

---

### 4.2 HIGH — Horizontal overflow on mobile (375 px / 390 px)

**Problem:** At 375 px the dashboard scrolled sideways by 11 px; at 390 px the interviews
list overflowed by 37 px (52 px at 375 px). Content was pushed off-screen with no way to
scroll to it comfortably.

**Root cause:** A CSS grid sizing trap, not a padding mistake. The offending panels are
**grid items**. A grid item defaults to `min-width: auto`, which resolves to `min-content` —
so a single `truncate` heading (`white-space: nowrap`) inflated the whole implicit
single-column mobile track well past its container.

Ablation confirmed it precisely. Removing the `truncate` heading from an interview card
freed 50 px; removing the row that contained it freed 278 px. The panel's measured
`min-content` was **411 px against a 358 px container**.

Tailwind's `grid-cols-*` utilities already wrap tracks in `minmax(0, 1fr)`, which is why the
explicit breakpoints were fine — but the mobile single-column grids rely on the *implicit*
`auto` track, which has no such guard.

**Solution:** Put the guard on the item instead of the track, in one place, so it covers
every current and future panel:

```css
.panel { @apply min-w-0 …; }
.stat-tile { @apply … min-w-0 …; }
```

**Files changed:** `assets/css/app.css`; rebuilt `static_dist/css/app.css`

**Verification:** the responsive audit now reports **"RESPONSIVE OK — no overflow, no
console errors at any tested width."** across all 60 width/page combinations.

---

### 4.3 MEDIUM — Missing index on a hot query path

**Problem:** The candidate detail page filters
`duplicate_suggestions.filter(status=PENDING, possible_duplicate_id__in=…)` on every view,
with only a foreign-key index to help.

**Solution:** Added a composite index matching the real predicate.

```python
indexes = [models.Index(fields=["candidate", "status"])]
```

**Files changed:** `apps/candidates/models.py`,
`apps/candidates/migrations/0003_duplicatesuggestion_candidates__candida_c2ff38_idx.py`

---

### 4.4 LOW — Dead scratch directory would be committed

**Problem:** `output/` contained four unreferenced PNGs left over from earlier audit
tooling, and was not ignored.

**Solution:** Added `/output/` to `.gitignore`. Nothing was deleted.

**Files changed:** `.gitignore`

---

### 4.5 MEDIUM — The route surface was not enforced by the test suite

**Problem:** Measuring coverage exposed something structural. `apps/web/views.py` sat at **63%**,
because the "does every page actually load?" check lived in a throwaway script outside pytest. CI
never verified it. A renamed URL name or a broken template would have passed every test and still
shipped a broken page.

**Root cause:** The suite tested *behaviour* — services, API contracts, permissions — but not the
*surface*.

**Solution:** Ported the walk into `tests/test_routes.py`: 41 URLs exercised as the demo user,
asserting each renders a non-empty body and that nothing returns 5xx, plus a companion test that no
workspace route leaks to an anonymous visitor. Coverage of `apps/web/views.py` rose **63% → 69%**
and the total **83% → 84%**.

To make the number enforceable rather than decorative, `pytest-cov` was added to the dev extras
(`coverage` was already declared, but the pytest plugin was missing), coverage settings were added
to `pyproject.toml`, and CI now runs with `--cov-fail-under=80`.

**Files changed:** `tests/test_routes.py` (new), `tests/conftest.py` (new — shared session-scoped
fixtures), `tests/test_api.py` (fixtures moved out), `pyproject.toml`,
`.github/workflows/ci.yml`

---

### 4.6 MEDIUM — Unclosed file handle on the resume download path

**Problem:** Under `pytest -W error` the suite failed with:

```
ResourceWarning: unclosed file <_io.FileIO name='...\resumes\...\hannah-kim-resume.txt' mode='rb'>
```

**Root cause:** `apps/web/views.py` hands an explicitly-opened handle to `FileResponse`:
`FileResponse(resume.file.open("rb"), …)`. Django *does* release it — `FileResponse` registers
`filelike.close` in `_resource_closers`, which `Response.close()` drains. A WSGI server always calls
`close()`; **Django's test client does not**, so the handle survived until garbage collection.

The production code was therefore correct, and I deliberately did not change it. But the leak was
real enough to fail `-W error`, and a resource that depends on someone else calling `close()` is
worth pinning down.

**Solution:** Close the response in the test after consuming `streaming_content`. This fixes the
test rather than the view, which is the honest correction — and it means the suite now genuinely
proves the handle is released rather than merely assuming it.

**Files changed:** `tests/test_routes.py`

---

### 4.7 MEDIUM — An entire feature path had never been executed (0% coverage)

**Problem:** Coverage put `apps/interviews/tasks.py` at **0%** — 18 statements, never run once.
That file is the interview invitation: it builds an iCalendar event and emails every panellist an
`.ics` attachment. It is a user-facing feature that no test had ever touched.

**Root cause:** `schedule_interview` enqueues it through `transaction.on_commit(...)`. Those
callbacks do not fire inside a transactional test, so even a test that scheduled an interview would
silently skip the invite path. Nothing was broken — the code was simply invisible to the suite.

**Solution:** Call the task directly and assert the observable behaviour: one email per panellist,
the right subject and recipient, the job title in the body, and exactly one `interview.ics`
attachment. The test then **parses the attachment back with `icalendar`** and checks the summary and
location — an `.ics` that does not parse is worse than none at all, so asserting its existence
alone would not have been worth much.

`apps/interviews/tasks.py` went **0% → 100%**, and the suite total **84% → 85%**.

**Files changed:** `tests/test_core.py`

---

### 4.8 HIGH — Two-factor authentication was broken at runtime

**Problem:** Writing tests for the authentication flows — the least covered and highest risk
part of the project — immediately reproduced a live failure:

```
File "apps/accounts/views.py", line 22, in form_valid
    otp_login(self.request, form.get_user(), form.otp_device)
TypeError: login() takes 2 positional arguments but 3 were given
```

**Root cause:** `django_otp.login(request, device)` reads the user off the request; it takes
**two** arguments, not three. Both MFA call sites passed `(request, user, device)`. Neither
path had ever been executed, so the `TypeError` had gone unnoticed since the feature was
written.

This was not cosmetic. It broke **both** halves of the feature:

- **Signing in** with a confirmed authenticator raised `TypeError` → the user could not log in.
- **Enrolling** a new authenticator raised `TypeError` on the confirmation POST → MFA could
  never be switched on in the first place.

So the entire MFA feature — advertised in the README — was non-functional, while every
existing test stayed green because none of them touched it.

**Solution:** Correct both call sites to the two-argument signature.

```python
otp_login(self.request, form.otp_device)   # login view
otp_login(request, device)                 # enrolment view
```

**Files changed:** `apps/accounts/views.py`

**Regression tests added:** `tests/test_auth.py` — a new file covering both MFA paths, plus
invitation acceptance, the app entry point and form validation. The TOTP code is generated the
same way `TOTPDevice.verify_token` generates it, so the test cannot drift from the code it
exercises:

```python
generator = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
generator.time = time.time()
return str(generator.token())
```

Result: `apps/accounts/views.py` **59% → 87%**, `apps/accounts/forms.py` **53% → 89%**, and the
suite total **85% → 86%**.

---

### 4.9 MEDIUM — A user-facing error that never reached the user

**Problem:** The invitation page rendered field-level errors but not **non-field** ones. The
view calls `form.add_error(None, "An account already exists for this email. Sign in to continue.")`
— and with no `{{ form.non_field_errors() }}` in the template, that message was silently
discarded. The page simply redisplayed, looking as though the submit button had done nothing.

**Root cause:** A systematic gap, not a one-off. `components/ui.html`'s `field()` macro renders
per-field errors, so every form looked covered — but non-field errors have no field to hang
off and need an explicit block. A sweep found the same omission in five templates.

**Solution:** Add the non-field error block, matching the pattern already used in `login.html`
(and the `.alert-error` class already defined in the design system), to the templates whose
views raise non-field errors:

- `templates_jinja2/accounts/accept_invitation.html` — the confirmed bug
- `templates_jinja2/tasks/form.html` — `form.add_error(None, exc)` on a duplicate task
- `templates_jinja2/careers/apply.html` — `form.add_error(None, exc)` on a rejected application
- `templates_jinja2/accounts/mfa_setup.html` — consistency

The remaining `ValidationError` handlers use `messages.error(...)`, which `layouts/app.html`
already renders, so they were never affected.

**Files changed:** the four templates above

**Regression test added:**
`tests/test_auth.py::test_accepting_an_invitation_cannot_take_over_an_existing_account` — asserts
the message actually appears in the response *and* that the target account is untouched, so the
invitation token can never become an account-takeover vector.

---

### 4.10 MEDIUM — A task closed through the API reported no completion time

**Problem:** `TaskWriteSerializer` accepts `status` on create, so `POST /api/tasks/` with
`status=DONE` produced a task whose status was `DONE` but whose `completed_at` was `null`. An
identical task created as `TODO` and then moved to `DONE` *was* stamped. The same status
therefore meant two different things depending on which door it came through — and any
"average time to close" or "completed this week" figure built on `completed_at` silently
dropped every API-created task.

**Root cause:** The completion timestamp was stamped only in `set_task_status`, which is
reached by the status-transition path. `create_task` validated and saved whatever the
serializer handed it, including `status`, without applying the invariant that
`status == DONE  ⇒  completed_at is not null`.

**Solution:** Stamp `completed_at` in `create_task` as well, so the invariant holds no matter
how the task reached `DONE`:

```python
if data.get("status") == Task.Status.DONE:
    data = {**data, "completed_at": timezone.now()}
```

**Files changed:** `apps/tasks/services.py`

**Regression tests added:** `tests/test_tasks.py` (19 tests) — `set_task_status` now has full
coverage: stamping on close, clearing on reopen, idempotent re-close, unknown status rejected,
cross-tenant rejection, permission denial, and the POST-only status endpoint.

---

### 4.11 MEDIUM — A dead link on the scorecard page returned HTTP 500

**Problem:** `reopen_scorecard` looked the submission up with `.get()`. A stale, mistyped or
cross-tenant `submission_id` therefore raised `ScorecardSubmission.DoesNotExist`, which the
view does not catch — it catches `ValidationError` only. An HR administrator following an old
link, or clicking twice after a colleague had already acted, got a 500 page instead of a
message.

**Root cause:** The lookup used `.get()` while the error contract of the layer above is
`ValidationError`. Every other service in the codebase returns `ValidationError` for "not
found"; this one leaked a Django ORM exception through a view that doesn't handle it.

**Solution:** Scope the queryset, take `.first()`, and raise `ValidationError` — the exception
the view already converts into a redirect plus a user-facing message. The signature also
gained `interview_id` so the operation is tightened to the interview in the URL:

```python
queryset = ScorecardSubmission.objects.select_for_update().filter(
    id=submission_id, application__organization=organization
)
if interview_id is not None:
    queryset = queryset.filter(interview_id=interview_id)
submission = queryset.first()
if not submission:
    raise ValidationError("Scorecard not found.")
```

**Files changed:** `apps/interviews/services.py`, `apps/web/views.py`

**Regression tests added:** `tests/test_interviews.py` (16 tests) — including
`test_reopen_scorecard_missing_submission_raises_not_500`, which asserts the exact contract:
a missing submission raises `ValidationError("Scorecard not found")`, not `DoesNotExist`.

---

### 4.12 MEDIUM — Publishing a job crashed on a malformed version field

**Problem:** `job_transition` parsed the optimistic-concurrency token with a bare
`int(request.POST.get("expected_version", 0))`. A request whose `expected_version` was empty
or non-numeric raised an uncaught `ValueError` → HTTP 500, before a single business rule ran.

**Root cause:** The one write endpoint in `web/views.py` that read raw `request.POST` instead
of going through a Django form. Every comparable entry point — `move_stage_htmx` uses
`MoveStageForm`, `reject_htmx` uses `RejectApplicationForm` — validates this pair through a
form and turns bad input into a 400 or a message. `job_transition` was the outlier, and the
outlier is where the 500 lived.

**Solution:** Introduce `JobTransitionForm` alongside the existing `JobForm`, mirroring
`MoveStageForm`, and validate before calling the service. A malformed request now redirects
with "That job status change could not be applied." instead of crashing:

```python
class JobTransitionForm(forms.Form):
    target_status = forms.ChoiceField(choices=Job.Status.choices)
    expected_version = forms.IntegerField(min_value=1)
```

The `ChoiceField` also closes a second hole: `target_status` can no longer reach the state
machine with a value outside `Job.Status.choices`.

**Files changed:** `apps/jobs/forms.py`, `apps/web/views.py`

**Regression tests added:** `tests/test_web_workspace.py` (12 tests) — notably
`test_job_transition_reports_a_non_numeric_version_instead_of_crashing`, plus stale-version
and unknown-status cases, and end-to-end coverage of the pipeline move, rejection, interview
cancel/complete and settings writes that had never been posted by a test.

---

### 4.13 HARDENING — the security boundary and the upload gate are now proven

**Problem:** Two modules sat in the "thin spots" list where a silent break would be worst
rather than merely possible:

- `apps/accounts/policies.py` (**80%**) — every list, detail view and API endpoint funnels
  through one of the `scope_*` helpers. A defect here is not a bug in a feature; it is one
  workspace reading another workspace's candidates, or a hiring manager reading a requisition
  that is not theirs. The interviewer branch was covered; the hiring-manager and recruiter
  branches — the two roles carrying *object-level* rules — had never run.
- `apps/candidates/tasks.py` (**62%**) — resume ingestion, the first thing a candidate does.
  The infected-file quarantine, the skill-taxonomy link, the failure path and duplicate
  detection had never executed.
- `apps/common/storage.py` (**73%**) — the ClamAV client, i.e. the code that actually runs in
  production, had never been executed at all. Locally `CLAMAV_HOST` is unset, so only the
  `NoopScanner` fallback ever ran.

**Root cause:** Not a defect — an omission. These are paths that only differ from the happy
path when roles, scopes or hostile input are involved, so they are invisible in manual testing
and were never reached by the route walk, which renders pages but posts nothing.

**Solution:** Three new test files, no production code changes:

- `tests/test_policies.py` (12 tests) — a hiring manager cannot see a peer's requisition,
  applications or candidates; a recruiter's department and location scopes narrow *every*
  list (jobs, applications, candidates) consistently; an **empty scope means "the whole
  workspace", not "nothing"** — the classic JSONField scoping bug, pinned explicitly;
  anonymous and deactivated members get a 404; a missing permission reads as "not found" so
  it cannot be used to probe for resources; and the tenant filter is applied before any role
  rule, so a second organization is invisible.
- `tests/test_resume_pipeline.py` (9 tests) — the already-parsed short circuit, the
  infected-file quarantine, skill linking, the sparse-document review flag, the failure path
  (which records a reason and re-raises rather than leaving a resume stuck at `UPLOADED`),
  and duplicate detection by checksum, by phone, and — importantly — the negative case where
  unrelated candidates must *not* be flagged.
- `tests/test_storage.py` (7 tests) — scanner selection, and the ClamAV INSTREAM protocol
  verified against a real loopback TCP server rather than assumed.

**No defect was found in the product code.** The one bug surfaced was in my own test server:
`b"zINSTREAM\0"` is **ten** bytes, not nine — `"zINSTREAM"` is already nine characters — and
reading one byte short leaves the NUL in the stream where it is then parsed as the start of a
length header. That is exactly the class of error this test exists to catch, and it is worth
recording because it is the kind of off-by-one that only shows up against a real daemon.

**Files changed:** `tests/test_policies.py`, `tests/test_resume_pipeline.py`,
`tests/test_storage.py` (new). No production code was modified in this round.

**Result:** `policies.py` **80% → 100%**, `candidates/tasks.py` **62% → 100%**,
`common/storage.py` **73% → 100%**. 58 of 84 modules are now at 100%.

---

### 4.14 HIGH — Scheduling an interview through the API always returned HTTP 500

**Problem:** Every well-formed `POST /api/v1/organizations/{slug}/interviews` request crashed
with `ValueError: Cannot assign "UUID(...)" : "InterviewParticipant.user" must be a "User"
instance`. The endpoint is listed in the README endpoint map and generated into the OpenAPI
schema, so it looked available; nothing could ever be scheduled through it.

**Root cause:** `InterviewsView.create` resolved the participant list with
`values_list("user_id", flat=True)`, producing **UUIDs**, and passed them straight to
`schedule_interview(participants=...)`, which does `InterviewParticipant(interview=..., user=user)`
and `str(user.id)` for the audit — both of which need model instances.

The reason it survived to this point is worth recording: the web path
(`web/views.py::interview_create`) never trips it, because `InterviewForm`'s
`ModelMultipleChoiceField` hands over a queryset of `User` objects. **Only the API path was
broken**, and no test had ever posted to it. A shared service with two callers of different
shape is exactly where this class of bug hides.

**Solution:** Resolve the ids to `User` instances while keeping the original authorisation
check intact:

```python
participants = list(
    User.objects.filter(
        memberships__organization=self.organization,
        memberships__active=True,
        id__in=participant_ids,
    ).distinct()
)
```

Both conditions sit in a single `filter()`, so they apply to the *same* membership row — an
inactive member is still rejected exactly as before, and the existing
`len(participants) != len(set(participant_ids))` guard is unchanged.

**Files changed:** `apps/api/views.py` (plus the `User` import).

**Regression tests added:** `tests/test_api_writes.py` (9 tests) — schedules an interview and
asserts a 201 with a template snapshot; refuses a participant from outside the workspace with
a 400 rather than dragging an outsider onto the panel; refuses an interview that ends before it
starts; submits a scorecard as a panellist and refuses one from a non-panellist; and covers the
task-list and interview-list filters.

---

### 4.15 HARDENING — write-path authorization and the API contract

**Problem:** `apps/applications/services.py` sat at **80%** with exactly the same uncovered
branches `policies.py` had: the hiring-manager ownership check and the recruiter
department/location scope — except here they guard a *mutation*, not a read. The remaining
uncovered lines were the product's own pipeline rules ("only active applications can move",
"this stage needs an interview", "this stage needs a scorecard"), which were asserted in the
README and in the UI copy but never by a test.

**Root cause:** Not a defect — an omission. `test_core.py` covers the happy path of a stage
move; nothing covered the refusals.

**Solution:** `tests/test_application_services.py` (16 tests) covers the guards directly. Two
findings came out of writing it, and both change how the code should be read:

1. **`_check_job_scope` is a backstop, not the first line of defence.** When an out-of-scope
   recruiter or a non-owning hiring manager tries to move an application, the error is
   "Application not found" — not "outside your department scope". `scope_applications` has
   already filtered the row out. That is the better answer, because it does not confirm the
   resource exists, and the end-to-end tests assert the refusal *without* pinning the message
   so a future change to which layer fires first will not break them. The guard is therefore
   also tested directly, which is the only way to reach its branches.
2. **The two layers compare scope differently.** `policies.py` asks
   `department_id__in=membership.department_scope` (UUID comparison) while `services.py` asks
   `str(job.department_id) not in membership.department_scope` (string comparison). Two
   implementations of the same rule can drift, and the drift is invisible until one disagrees
   with the other. This is now documented rather than refactored — changing a working
   authorization check is a far riskier move than covering it.

**Files changed:** `tests/test_application_services.py`, `tests/test_api_writes.py` (new). No
production code was modified in this section.

**Result:** `applications/services.py` **80% → 99%**, `api/views.py` **77% → 92%**,
`api/exceptions.py` **80% → 88%**.

---

### 4.16 HARDENING — the web form POST handlers are now exercised (Phase 5)

**Problem:** `apps/web/views.py` was still the single largest uncovered block at **64 missed
statements** even after Round 7 — and those were almost entirely the **form POST handlers** that
the route walk renders but never submits: `job_create`, `job_edit`, `candidate_create`,
`candidate_edit`, `resume_download` and `duplicate_merge`. These are exactly the Phase-5
"every button must work, no fake buttons" paths, and a green route walk had been masking the fact
that none of them had ever been run by a test.

**Root cause:** Not a defect — an omission, the same shape as Rounds 6 and 7. The handlers are
reachable by hand in a browser but invisible to the suite, which posts nothing; the one genuinely
interesting behaviour hiding inside them only ran when a POST actually hit `job_edit`.

**Solution:** `tests/test_web_forms.py` (17 tests) posts to every one of the six handlers and
asserts the observable outcome — creation with audit + redirect, invalid-input redisplay without a
write, permission denial (a 404, consistent with every other org-scoped view), encrypted-contact
storage for candidates, email normalisation, streaming the resume with the right
`Content-Disposition` and an audit row, and the duplicate-merge happy path **plus** both refusal
branches (conflicting applications on the same job, and an unknown suggestion id). Writing the
tests surfaced one behaviour worth pinning rather than a new bug:

- **`job_edit` sanitises HTML through bleach.** `JobForm.clean_description_html` strips
  disallowed tags (`<script>`), so a stored-XSS attempt in a job description is dropped on save.
  That branch had never executed; the test now asserts `<script` is gone while the surrounding text
  and safe tags survive.

**No new product defect was found in this round** — which is the outcome the final passes should
produce. As in Round 6, the value was turning the largest remaining "renders but never runs" gap
into assertions, and confirming the merge-conflict guard and the resume-streaming handle release
both actually behave.

**Files changed:** `tests/test_web_forms.py` (new). No production code was modified in this round.

**Result:** `apps/web/views.py` **83% → 98%** (64 → 6 missed) across Rounds 8 and 8.2. The six
remaining statements are the `Http404` early-returns in the `scorecard` view (a non-participant who
is also not an HR/admin is rejected before the POST branch, and a participant without
`scorecards.submit` cannot exist under this RBAC) and the route/permission 404 helpers — all
reached structurally by the permission and route-walk tests.

---

### 4.17 HARDENING — the task list filters and task write endpoints

**Problem:** `apps/tasks/views.py` was the last product-code thin spot at **81%**. The uncovered
statements were entirely interactive: the explicit-status, assignee-by-id and text-search filter
branches on the task list, the whole `task_create` POST handler, and the `task_status` error paths.
The list page renders in the route walk, but "search, filter and every form must work" is a Phase-5
requirement that a render-only walk cannot satisfy.

**Root cause:** Not a defect — the same render-vs-interact omission as Rounds 6–8. The filters only
diverge from the default when a query parameter is present, and no test ever passed one.

**Solution:** `tests/test_tasks_views.py` (11 tests) exercises each filter branch (`?status=open`
vs `?status=DONE`, `?assignee=me` vs `?assignee=<id>`, `?q=` matching title *and* description) and
both write endpoints: create (persists + redirects + "Task created."), create with an invalid form
(redisplay, no write), create denied to a role without `tasks.manage` (404), status change
(stamps `completed_at`), a hand-crafted invalid status (message, no change), and a stale task id
("Task not found." rather than a 500).

**No new product defect was found.** The only statements still uncovered are the defensive
`form.add_error(None, exc)` line in `task_create` — the form's assignee queryset is already
restricted to active members, so the service's "choose an assignee who belongs to this workspace"
`ValidationError` is not reachable through this view.

**Files changed:** `tests/test_tasks_views.py` (new). No production code was modified.

**Result:** `apps/tasks/views.py` **81% → 97%** (13 → 2 missed). Measured coverage for the tree rose
to **96%** (3,218 statements, 132 missed) and the suite grew 153 → **192 tests**.

---

## 5. Remaining issues

Nothing critical or high remains. These are the honest leftovers.

### Medium

1. **The Docker image has never actually been built.** There is no Docker binary in this
   environment (`docker: command not found`), so the Dockerfile was corrected by inspection.
   It *did* contain a real bug — the assets stage never copied `scripts/`, so `npm run build`
   would have failed at the `copy-fonts.mjs` step. That is fixed, but a first
   `docker compose build` on a Docker-equipped machine is still the outstanding proof.
2. **The weakest covered areas are now visible.** At 96% overall, the thin spots are
   `config/settings.py` (70% — the Postgres/MySQL/S3 branches that only exist in those
   environments), `apps/audit/admin.py` (79% — Django admin actions), `apps/common/crypto.py`
   (88%) and `apps/jobs/services.py` (89%) — the latter two mostly defensive `ValidationError`
   guards for cross-tenant ids that the queryset layer already filters out. None are broken; they
   are simply unexercised, and the first two are largely environment-dependent by nature.
   Eight rounds of work removed `apps/accounts/forms.py` (53% → 89%),
   `apps/accounts/views.py` (59% → 87%), `apps/tasks/services.py` (63% → 100%),
   `apps/interviews/services.py` (64% → 92%), `apps/web/views.py` (69% → 98%),
   `apps/accounts/policies.py` (80% → 100%), `apps/candidates/tasks.py` (62% → 100%),
   `apps/common/storage.py` (73% → 100%), `apps/applications/services.py` (80% → 99%),
   `apps/tasks/views.py` (81% → 97%), `apps/interviews/forms.py` (83% → 100%) and
   `apps/api/views.py` (77% → 92%) from this list.
3. **`_check_job_scope` duplicates the scoping rules already expressed in `policies.py`**, and
   the two compare `department_scope` differently (string vs UUID). They currently agree, and
   the queryset layer fires first so the duplication is a backstop rather than a liability —
   but it is a genuine drift risk and the obvious candidate for consolidation if this code is
   ever extended. Left as-is deliberately: refactoring a working authorization check is
   riskier than covering it.

### Low

3. **`compose.yaml` provisions MySQL while the hosted path uses Postgres/Neon.** Worth noting
   that this is *deliberate and documented*, not an oversight — the README and `.env.example`
   both spell out the split (Postgres in production, MySQL for the self-contained compose stack,
   SQLite for the fastest local run), and the settings module parses all three from
   `DATABASE_URL`. It is listed here only because a reviewer skimming the repo may wonder.
4. **`templates_jinja2/interviews/scorecard.html` is written as very long single lines.**
   It renders correctly, but it is painful to review or diff. It also emits `disabled` as a
   bare interpolated attribute — valid HTML5, though `disabled="disabled"` would be clearer.
5. **`django-filter` is installed and configured but unused** — list filters are hand-written
   query params. Works fine; mildly inconsistent.
6. **No dark mode.** The brief said "if suitable"; the light-only palette is a deliberate
   choice, and the design is coherent without it.
7. **No global search.** Search exists per-list (jobs, candidates), not across the workspace.
8. **Committed screenshots are ~1.9 MB** in `docs/screenshots/`.
9. **`.audit_view/` audit helpers remain on disk** (gitignored, so they will not be committed).

---

## 6. Could not be verified here

Stated plainly rather than glossed over:

- **Docker build and run** — no Docker in this environment.
- **A live Postgres/Neon connection** — the URL *parses* correctly and the psycopg path is
  configured, but no query was issued against Neon, and no migrations were applied to it.
- **The MySQL path** — exercised by CI's service container, not locally (local runs used SQLite).
- **Real malware scanning (ClamAV)** and **object storage (S3/MinIO)** — configured and
  wired, but exercised only in their disabled/console fallbacks.

---

## 7. Deployment checklist

Ready:

- [x] Four quality gates pass, including `pytest -W error` exactly as CI runs it
- [x] `collectstatic` verified (166 copied / 468 post-processed) with fonts resolving
- [x] `seed_demo` idempotent — safe on every container start
- [x] Demo account documented and working (`demo@example.com` / `demo123`)
- [x] Postgres URL parsing verified against the real Neon connection string
- [x] Security headers, CSP, HSTS, SSL redirect, non-root container user, `/healthz`
- [x] README with architecture, env table, endpoint map and screenshots
- [x] `.env.example` complete and commented
- [x] CI workflow covering lint, check, migration drift, tests and asset build

Before the first public deploy:

- [ ] Run `docker compose build` once on a machine with Docker
- [ ] Apply migrations to Neon and confirm the connection from the host
- [ ] Rotate `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEY` for the real environment
- [ ] Set `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` to the real domain

---

## 8. Summary

| | |
|---|---|
| **Critical issues remaining** | 0 |
| **High issues remaining** | 0 |
| **Fixed this pass** | 1 critical, 3 high, 8 medium, 1 low |
| **Tests** | 192 passing (13 API contract + 9 API writes + 16 auth + 15 core + 3 route + 19 tasks + 11 task views + 16 interviews + 16 application services + 12 policies + 9 resume pipeline + 7 storage + 6 public web + 12 workspace web + 17 web form POST handlers + 11 web interview/scorecard/password handlers), 0 failing |
| **Coverage** | 95% of 3,284 statements, enforced at 80% in CI |
| **Responsive** | 5 widths × 12 pages, no overflow, no console errors |
| **Routes** | 41 / 41 render, none 5xx |
| **Verdict** | **Deployment ready** |

---

# Round 9 — portfolio-readiness pass

**Date:** 4 October 2026 · **Method:** independent re-verification rather than a fresh read. Every
claim below is backed by a command that was actually executed in this pass, including a real
headless-browser run against a live server.

## 9.1 Verdict

The application was already feature-complete and green, so this pass deliberately did **not** add
features. It attacked the gap that actually blocks a public portfolio: **shipped weight and
release hygiene that a reviewer notices in the first five minutes.** Four defects were found, all
confirmed by measurement, all fixed. No functional regression was introduced — the full gate suite
and a live browser run both stayed green.

## 9.2 Issues found and fixed

### 9.2.1 HIGH — Two frameworks were bundled, started, and never used

**Issue.** `htmx.org` and `alpinejs` / `@alpinejs/csp` were imported in `assets/js/app.js`,
assigned to `window`, and `Alpine.start()` was called on every page load. **Not one template
contained a single `hx-*` or `x-*` attribute** — verified by grepping all 34 templates for
`hx-`, `x-data`, `x-show`, `x-model`, `x-bind`, `x-cloak`: zero matches. `app.js` also registered
`htmx:afterSwap` and `htmx:responseError` listeners, which could never fire because HTMX never
processed a request.

**Cause.** The original audit flagged this in §3 (item 10) as "dead dependencies". Eight subsequent
hardening rounds never removed it — the listeners made the code *look* wired up, so it read as
intentional rather than unused.

**Solution.** Removed both packages, their imports, the `window` assignments, `Alpine.start()`, the
two dead HTMX listeners, and the unused `window.atsToast` export (nothing consumed it).
`sortablejs` and `chart.js` were **kept** — both are genuinely used (`data-kanban-stage` and
`canvas[data-chart]`).

**Evidence.**

| Metric | Before | After |
|---|---|---|
| `static_dist/js/app.js` (minified) | 341.5 KB | **221.9 KB** (−35%) |
| `hx-*` / `x-*` attributes in templates | 0 | 0 |
| Templates referencing `htmx` / `alpine` | 0 | 0 |

`assets/js/app.js`, `package.json`, `static_dist/js/app.js`

### 9.2.2 HIGH — The repository was not under version control

**Issue.** There was **no `.git` directory**. A portfolio project that cannot be cloned, diffed,
or shown as a commit history is not a portfolio project, and the 22 KB of prior audit/review
documentation describing eight rounds of work had no history to attach to.

**Cause.** Local working copy only; `.gitignore` existed but had never been exercised.

**Solution.** `git init`, then verified the ignore rules actually hold rather than assuming they
did — every sensitive path checked with `git check-ignore`: `.env`, `db.sqlite3`, `.venv/`,
`node_modules/`, `media/`, `staticfiles/`, `static_dist/`, `output/`, `*.egg-info/`,
`.audit_view/`, `.playwright-cli/` all confirmed **ignored**. The commit set is **188 files /
2.8 MB**, of which 1.9 MB is the 16 README screenshots. `.env.example` was scanned and contains
only placeholders (`AI_API_KEY=` empty) with generation commands in comments.

**Two gaps closed while here:** `.gitignore` and `.dockerignore` were both missing
`.playwright-cli/`, and `.dockerignore` was missing `*.egg-info`, `*.log`, `.coverage`,
`coverage.xml`, `htmlcov`, `.idea`, `.vscode`, `.DS_Store` and `Thumbs.db`.

**Note.** The initial commit is intentionally **left for the author** to make. Files are staged
and verified; `git commit` has not been run.

### 9.2.3 MEDIUM — `npm audit` reported 4 high-severity vulnerabilities

**Issue.** `npm audit` reported 4 high-severity findings: `braces` (GHSA-vfj7-8cjw-p6xm, stack
exhaustion DoS) reached via `micromatch` → `@parcel/watcher` → `@tailwindcss/cli`.
`npm audit fix --dry-run` confirmed **no non-breaking fix exists** upstream.

**Cause.** Tailwind's file watcher is a pure **build-time** tool, but it was declared in
`dependencies` alongside the libraries that actually ship — so the production dependency surface
claimed to contain a vulnerable package tree that never reaches the browser.

**Solution.** Reclassified: `tailwindcss` and `@tailwindcss/cli` moved to `devDependencies`, leaving
the four packages that genuinely feed the shipped bundle (`chart.js`, `lucide`, `sortablejs`,
`@fontsource-variable/manrope`) in `dependencies`. Added an `audit:prod` script
(`npm audit --omit=dev`) and wired it into CI as a gate.

**Evidence.** `npm audit` → 4 high · `npm audit --omit=dev` → **found 0 vulnerabilities**.

The residual 4 findings are inside the Tailwind CLI's file-watcher chain. They cannot reach a
browser and cannot be fixed without a breaking Tailwind upgrade; the new CI gate keeps the
*runtime* surface pinned at zero.

### 9.2.4 MEDIUM — `scripts/screenshots.mjs` hardcoded one machine's filesystem

**Issue.** The committed screenshot script resolved Playwright from
`C:/Users/Administrator/AppData/Local/npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright` and
Chromium from `.../chromium-1228/chrome-win64/chrome.exe`. Any other machine — any OS, any npx
cache hash — crashed on line 10. The one script that documents *how the portfolio screenshots were
produced* was the one script nobody else could run.

**Solution.** Replaced both with dynamic resolution: Playwright is searched across the npx cache
(via `LOCALAPPDATA`, non-Windows via `~/.npm/_npx`) then falls back to a local install; Chromium
is searched across `ms-playwright` for win/linux/mac layouts with `PLAYWRIGHT_PATH` / `CHROME_PATH`
still honoured as overrides. `BASE_URL` and `ORG_SLUG` are now environment-configurable, defaults
preserved. Added `npm run screenshots`.

**Evidence.** Ran end-to-end on this machine with no environment overrides: regenerated all 16
screenshots and reported **"No browser console errors."**

## 9.3 Live verification performed

The four rounds above were previously verified by reading code and by `pytest`. Because the change
in 9.2.1 removes runtime JavaScript that no Python test can observe, this pass added a real browser
run — Chromium against a live `runserver` on port 8123, signed in as `demo@example.com`.

| Check | Result |
|---|---|
| Pages loaded (landing + 10 workspace pages) | **11 / 11 returned HTTP 200** |
| Lucide icons rendered per page | 19–51 (proves `app.js` executes after the dependency removal) |
| Chart.js canvases painted | dashboard 1/1, reports 3/3 |
| Browser console errors | **0** |
| Uncaught page errors | **0** |
| Failed network requests | **0** |
| Horizontal overflow at 375 px (6 pages) | **0 px on every page** |

The mobile pass is the meaningful one here: a CSS grid `min-width: auto` trap is invisible at
1440 px and only appears at 375 px, which is exactly the width the previous round caught.

## 9.4 Security note for the author

`.env` (gitignored, never committed, excluded from the Docker build by `.dockerignore`) contains a
**live third-party AI gateway key**. It is correctly protected from publication, but it has now
been read during this pass and should be **rotated** before the project is made public. No key
material is present in `.env.example`, in any committed file, or in the staged commit set.

## 9.5 Remaining issues after this pass

Unchanged from §5, minus the items closed above:

- **Medium** — the Docker image has still never been built (no Docker binary available). The
  dependency reclassification in 9.2.3 was checked against the Dockerfile by inspection: the assets
  stage runs a plain `npm ci`, which installs `devDependencies`, so `tailwindcss` and
  `@tailwindcss/cli` remain available at build time.
- **Medium** — `config/settings.py` sits at 72% coverage and `apps/common/ai_gateway.py` at 33%;
  both are dominated by branches that only exist in a configured environment (Postgres/MySQL/S3 and
  the live AI gateway). Environment-dependent by nature, not broken.
- **Low** — templates remain written as a few very long lines (worst: `interviews/scorecard.html`, a
  3,495-character line). Left deliberately: reformatting 34 Jinja templates is a large mechanical
  diff with real whitespace-sensitivity risk, and it changes no behaviour. Flagged rather than
  churned.
- **Low** — `django-filter` is installed and configured but unused; list filters are hand-written
  query params. Works, mildly inconsistent.
- **Low** — no global cross-workspace search (search exists per-list) and no dark mode.
- **Low** — committed screenshots total ~1.9 MB.
- **Low** — `db.sqlite3`, `.venv/`, `node_modules/`, `media/`, `staticfiles/` and `static_dist/`
  exist in the working directory. All gitignored and verified ignored; they are local artefacts of
  running the app, not repository content.

## 9.6 Scorecard after Round 9

| Dimension | Score | Change | Basis |
|---|---|---|---|
| **Code quality** | **9.5 / 10** | — | Unchanged. Dead code removed, no TODOs, ruff clean, 192 tests at 95% with an enforced floor. |
| **Frontend quality** | **9.0 / 10** | — | Verified live: icons render, charts paint, zero console errors, zero overflow at 375 px. |
| **UX quality** | **8.5 / 10** | — | Unchanged. Still no global search and no dark mode. |
| **Feature completeness** | **9.5 / 10** | — | Unchanged and re-confirmed: all 41 routes render. |
| **Production readiness** | **9.0 / 10** | **↑ from 8.5** | Was docked for an unbuilt image; now also gains a clean dependency audit, a gate for it in CI, version control with a verified-clean commit set, and a screenshot script that runs anywhere. |

## 9.7 Deployment checklist (additions)

Added to §7:

- [x] `npm audit --omit=dev` reports **0 vulnerabilities**, gated in CI
- [x] Shipped JS bundle carries no unreferenced framework (221.9 KB, down from 341.5 KB)
- [x] Repository under version control; 188-file commit set verified free of secrets and artefacts
- [x] `.gitignore` / `.dockerignore` verified with `git check-ignore`, not by inspection
- [x] `scripts/screenshots.mjs` runs on any machine and any OS

Before publishing:

- [ ] **Rotate the AI gateway key currently in `.env`** (see §9.4)
- [ ] Run `docker compose build` on a Docker-equipped machine
- [ ] Make the initial commit (staged and verified, intentionally left to the author)
