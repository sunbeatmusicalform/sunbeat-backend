# Market-readiness P0 evidence — 2026-08-07

Branch: `codex/market-readiness-p0-hardening`, based on production/main commit
`405987c5a0cb6600c7297856dea5a18211e79b1b`.

Draft review: https://github.com/sunbeatmusicalform/sunbeat-backend/pull/59

No production deploy, merge, schedule, DNS change, customer write, or Atabaque
onboarding operation was performed. On 2026-08-08 Felipe authorized the two
additive Supabase migrations described below; no production application release
followed. Isolated QA releases were used for browser and operational evidence.

## Automated evidence

- Backend: `212 passed` on Python 3.12.13 using the existing isolated venv.
- Python compile check: `python -m compileall -q app scripts tests` passed.
- Patch hygiene: `git diff --check` passed for source, configuration, docs, and
  tests. Generated Vite bundles are excluded because bundled Three.js shader
  strings preserve upstream whitespace.
- Frontend targeted lint passed for `src/lib/api.ts`, `src/pages/LoginPage.tsx`,
  `src/portal/OnboardingPanel.tsx`, and `src/portal/Portal.tsx`.
- Frontend production build passed (`tsc -b && vite build`, 2,825 modules).
- Frontend source is preserved locally through commit `6211ba2` on
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
| Magic link | Ready in branch / schema applied / QA approved | Hashed persistent token, 30-minute expiry, atomic one-use consumption, replay/cross-tenant tests and isolated browser/inbox QA | Application code is not deployed to production |
| Portal session | Ready in branch / QA approved | User/workspace/session binding, membership check, expiry, logout/revocation tests; logout revocation observed in QA logs | Managed password sessions remain legacy/stateless |
| Authorization metadata | Ready in branch | Self-service and retention claims use server-controlled `app_metadata`; regression test rejects user-editable metadata | Existing managed users remain managed by default |
| Shared rate limiting | Ready in branch / schema applied | Atomic database limiter for IP and IP+subject; transactional boundary test passed; Fly secret-name audit confirmed a deployed service-role key without reading its value | Application code is not deployed |
| Self-service onboarding | Ready in branch / QA approved | Isolated signup, inbox magic link, signed preview, apply, idempotent refresh, portal recovery and logout validated | Production deploy still requires approval |
| Managed clients / Atabaque | Preserved | Existing `profile_only` behavior and managed test; no customer write performed | No live write test by design |
| EN/PT-BR recovery UX | Ready in built bundle | Localized replay/access/expired-preview feedback and localized loading | Broader portal UI is historically PT-first |
| Waitlist / Enterprise | Ready in branch / QA approved | Validation, honeypot, persistence and real Resend delivery to the approved recipient were observed | Production deploy still requires approval |
| Public chat | Ready | Home bundle still omits public ChatDemo; contextual portal helper remains | None identified in this slice |
| Free 60-day assets | Ready but unscheduled / schema applied | Registry, access 410, idempotent deletion tests and real dry-run on 2026-08-08: 0 eligible, 0 deleted, 0 failed | Apply/schedule blocked until isolated deletion and restore drill; pre-registry assets need reviewed backfill |
| 5xx / health / readiness | Code ready | Request IDs, 5xx logs, liveness plus service-role/database/schema readiness tests | Alert provider/routing not configured |
| CORS / headers / secrets | QA verified | QA returned CSP/HSTS/request ID and security headers; denied an untrusted origin and allowed `sunbeat.pro`; current-tree scan found placeholders/test values only | Alerting and legacy Supabase advisor findings remain separate work |
| Backup / restore | Backups verified; restore blocked | Eight completed daily physical backups listed for 2026-08-01 through 2026-08-08; WAL-G active, PITR disabled | Restore drill is not executed; never restore over the real project |
| Terms / Privacy / LGPD | Blocked by legal/content decision | Acceptance versions stored and checklist documented | Actual bilingual legal documents/routes are absent |
| Commit / PR | Draft PR #59 open | Small thematic implementation commits and this evidence update are published for review | No merge or production deploy is authorized |

## Manual QA credential step — completed 2026-08-08

Felipe used the disposable self-service workspace `sunbeat-qa-20260808-1108`
(never Atabaque for onboarding writes), accessed its inbox, completed
MotoSchema, verified the generated form, refreshed the configured portal,
logged out, and approved the authentication test. Waitlist and Enterprise
messages arrived at `contatofelipefonsek@gmail.com`.

That address is temporarily also the owner/contact email for the managed
Atabaque account. It must be replaced with the client's definitive address only
after the tool is finalized and presented, in a coordinated handoff. No account
or Atabaque configuration was changed during this QA work.

Separate operational approvals remain necessary for the restore drill, alert
routing, retention backfill/apply/schedule, managed-account email handoff,
merge, and Fly production deploy.

## Operational evidence — 2026-08-08

- Ran `scripts.enforce_free_asset_retention` in its default dry-run mode from
  the isolated Fly QA app against the applied registry: `eligible=0`,
  `deleted=0`, `missing=0`, `failed=0`, `dry_run=true`. The command omitted
  `--apply`, so it performed no Storage deletion or database update.
- QA `/login` returned CSP, HSTS, `X-Content-Type-Options`, referrer and
  permissions policies, no-store caching, and a request ID. The official
  horizontal Sunbeat logo was visually verified after replacing the temporary
  text-only wordmark.
- A CORS preflight from `https://evil.example` returned 400 without an allowed
  origin; `https://sunbeat.pro` returned 200 with that exact allowed origin.
- Supabase CLI 2.109.1 listed eight completed physical daily backups from
  2026-08-01 through 2026-08-08. WAL-G is active and PITR is disabled. This is
  backup-availability evidence, not a restore drill.
- Current-tree secret scan found empty `.env.example` fields, CI placeholders,
  and test-only values; it did not find a deployed credential value. Fly and
  Supabase secret values were not read.

## Follow-up security audit — 2026-08-08

- Reviewed current Supabase changelog and guidance for RLS, Data API grants,
  function privileges, `security invoker`, and authorization metadata.
- Replaced authorization reads from user-editable `user_metadata` with
  server-controlled `app_metadata`.
- Replaced the two public RPC functions' `security definer` mode with
  `security invoker`, retained explicit service-role-only execution grants, and
  set an empty search path with qualified relations/functions.
- Added a read-only readiness schema gate for every new security/retention
  table.
- Read-only Fly audit reconfirmed release 251 at commit `405987c`, a started
  machine with passing health, and deployed secret names including
  `SUPABASE_SERVICE_ROLE_KEY`; no secret value was retrieved.

## Authorized Supabase migration evidence — 2026-08-08

- Authenticated the Supabase CLI through the official browser device flow and
  linked only project `sunbeat-core` (`pjawmgcnccrdcpjmworg`, `sa-east-1`).
- Applied additive migrations `20260808125636_self_service_security.sql` and
  `20260808125637_asset_retention.sql`; repaired migration history after the
  CLI dry-run path failed before executing SQL, and confirmed local/remote
  migration versions match.
- Confirmed all five new tables exist with RLS enabled, no `anon` or
  `authenticated` CRUD grants, and explicit `service_role` CRUD grants.
- Confirmed both RPC functions are `security invoker`, have an empty
  `search_path`, deny execution to `public`/`anon`/`authenticated`, and grant
  execution to `service_role` only.
- Ran an isolated transaction as `service_role`: first magic-link consumption
  succeeded, replay failed, cross-workspace consumption failed, and a limit of
  two allowed calls rejected the third call. The transaction was rolled back;
  a follow-up query confirmed zero QA magic-link and rate-limit rows remained.
- Supabase advisors reported no finding on the five new tables or two new
  functions. They did report pre-existing public tables without RLS, mutable
  search paths, duplicate indexes, policy init-plan warnings, and disabled
  leaked-password protection. These were not changed blindly because several
  tables serve existing production workflows; remediation requires a separate
  policy/compatibility review.
