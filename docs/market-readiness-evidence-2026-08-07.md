# Market-readiness P0 evidence — 2026-08-07

Branch: `codex/market-readiness-p0-hardening`, based on production/main commit
`405987c5a0cb6600c7297856dea5a18211e79b1b`.

Draft review: https://github.com/sunbeatmusicalform/sunbeat-backend/pull/59

No production deploy, merge, migration, schedule, DNS change, customer write,
or Atabaque onboarding operation was performed.

## Automated evidence

- Backend: `204 passed` on Python 3.12.13 using the existing isolated venv.
- Python compile check: `python -m compileall -q app scripts tests` passed.
- Patch hygiene: `git diff --check` passed for source, configuration, docs, and
  tests. Generated Vite bundles are excluded because bundled Three.js shader
  strings preserve upstream whitespace.
- Frontend targeted lint passed for `src/lib/api.ts`, `src/pages/LoginPage.tsx`,
  `src/portal/OnboardingPanel.tsx`, and `src/portal/Portal.tsx`.
- Frontend production build passed (`tsc -b && vite build`, 2,825 modules).
- Frontend source is preserved locally in commit `dd6c567` on
  `codex/market-readiness-p0-frontend`; the backend PR carries its built bundle.
- Full frontend lint remains blocked by 18 pre-existing findings outside the
  changed files (Fast Refresh export layout and React purity/effect rules).
- Docker validation was unavailable because the configured Colima socket was
  not running. The Python 3.12 suite matches the Dockerfile's Python 3.11+
  language requirements; a future CI/container build is still required.

The QA mock E2E test uses the isolated slug `qa-isolated-records` and covers:

1. signup and workspace/user creation;
2. persisted magic-link issuance and first consumption;
3. rejection of replay and cross-workspace membership;
4. user/workspace-bound persistent portal session;
5. MotoSchema initial state, signed preview, apply, retry, and refresh;
6. stable `completedAt` on idempotent retry;
7. logout/revocation and rejection after logout.

## Readiness matrix

| Area | Status | Evidence / next action | Residual risk |
|---|---|---|---|
| Magic link | Ready in branch | Hashed persistent token, 30-minute expiry, atomic one-use consumption, replay/cross-tenant tests | Requires security migration before deploy |
| Portal session | Ready in branch | User/workspace/session binding, membership check, expiry, logout/revocation tests | Managed password sessions remain legacy/stateless |
| Shared rate limiting | Ready in branch | Atomic database limiter for IP and IP+subject; fail-closed tests | Requires migration and service-role backend key |
| Self-service onboarding | Ready in branch | Signed preview, profile binding, compensation on persistence failure, idempotent apply/refresh | Manual inbox/browser QA still required |
| Managed clients / Atabaque | Preserved | Existing `profile_only` behavior and managed test; no customer write performed | No live write test by design |
| EN/PT-BR recovery UX | Ready in built bundle | Localized replay/access/expired-preview feedback and localized loading | Broader portal UI is historically PT-first |
| Waitlist / Enterprise | Ready in branch | Validation, honeypot, persistent record, provider result, confirmed recipient test | Real Resend delivery requires QA credential/inbox |
| Public chat | Ready | Home bundle still omits public ChatDemo; contextual portal helper remains | None identified in this slice |
| Free 60-day assets | Ready but unscheduled | Registry, access 410, dry-run default, idempotent deletion/missing/error tests | Existing pre-registry assets require a separately reviewed backfill |
| 5xx / health / readiness | Code ready | Request IDs, 5xx logs, liveness and DB readiness tests, Fly checks | Alert provider/routing not configured |
| CORS / headers / secrets | Code ready | Trusted hosts, explicit CORS surface, CSP/HSTS/security headers; secret scan found placeholders only | CSP needs browser verification in deployed QA |
| Backup / restore | Blocked by credential/manual drill | Exact isolated restore drill documented | Backup must not be called tested yet |
| Terms / Privacy / LGPD | Blocked by legal/content decision | Acceptance versions stored and checklist documented | Actual bilingual legal documents/routes are absent |
| Commit / PR | Draft PR #59 open | Four thematic implementation commits plus this evidence update are published for review | No merge or deploy is authorized |

## Single manual QA credential step

Create one disposable self-service QA email/workspace (never Atabaque), access
its inbox, and run the domain matrix on `sunbeat.pro` and `sunbeat.com.br`:
first magic link succeeds, replay fails, onboarding completes and survives
refresh, logout revokes access, and a waitlist/Enterprise message arrives at
`contatofelipefonsek@gmail.com`. This is the only application-flow step that
cannot be proven by the repository mocks.

Separate operational approvals remain necessary for database migrations,
Resend inbox evidence, restore drill, alert routing, retention backfill/dry-run,
schedule activation, merge, and Fly deploy.
