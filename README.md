# Fraud360

A **multi-tenant, white-labelable Fraud Risk Management and Early Warning Signals
platform** for Indian banks, aligned to the RBI Master Directions on Fraud Risk Management
(July 2024). It is both the control plane — where you **onboard banks (tenants)**,
**brand** each tenant's surface and **configure** their detection rules — and the runtime
that ingests transactions, scores them and carries a signal through to a regulatory
filing.

Built as **n-tier microservices** in **Python + FastAPI + PostgreSQL**.

> **On Tazama.** The per-tenant rule format follows [Tazama](https://tazama.org)'s *band
> shape* — an ordered set of bands, each with a threshold and a sub-rule reference — and
> the console's structured editor edits that shape. **Nothing in this repository runs
> Tazama.** There is no Tazama dependency, no ArangoDB or NATS, and no call to a Tazama
> service; detection is implemented natively in
> `services/analytics_service/app/detection/` against PostgreSQL. The shared schema means
> a Tazama deployment could be driven from the same control plane later, but that is a
> possibility, not the current architecture.

See `docs/FRMS-EWS-HLD-v1.1.pdf` for the architecture and `docs/FRMS-EWS-BRD-v1.2.pdf`
for the numbered requirements and release gates.

---

## 1. Architecture

### Tiers (n-tier), per service
```
Presentation tier   →  routes/*        (FastAPI controllers, DTO validation)
Business tier       →  services.py     (domain logic, orchestration)
Data-access tier    →  repositories.py (all SQL; persistence-agnostic above)
Data tier           →  models.py + PostgreSQL
```

### Services (microservices)
```
                         ┌───────────────────────────┐
   Browser ──▶  Gateway  │  edge auth · routing ·     │  serves the Admin Console (SPA)
        :8080            │  tenant-context injection  │
                         └─────────────┬──────────────┘
      ┌───────────────┬────────────────┼────────────────┬───────────────┐
      ▼               ▼                ▼                ▼               ▼
 tenant:8081    branding:8082    config:8083     analytics:8084   ingestion:8085
 registry ·     white-label      versioned       metrics · RFA    rail · file ·
 users · auth   themes ·         detection       lifecycle ·      ISO 20022 ·
 MFA · SSO ·    theme.css /      rule and        filings · board  CBS events ·
 sessions       theme.json       typology        packs · usage    dedupe · queue
      │               │          configs              │               │
      │               │              │           notification:8087     │
      └───────────────┴──────────────┴────────────────┴───────────────┘
                                     ▼
                    PostgreSQL — 10 schemas, one owner each, 8 login roles
```

**Multi-tenancy model:** logical isolation by `tenant_id` in every query, enforced in
`cp_common.resolve_tenant_scope` (platform staff may act across tenants; every tenant-scoped
role, whatever it is called, is pinned to its own tenant), on top of **schema-per-service** ownership — each service connects
as its own PostgreSQL role that can reach only its own schemas, so a reach for a
neighbour's table fails in the database rather than by convention (`scripts/provision_db_roles.py`).
There are **no cross-service foreign keys** so each service stays independently deployable
— referential integrity is enforced in the application layer.

### Folder structure
```
control-plane/
├─ docker-compose.yml         # postgres + services (one shared image)
├─ Dockerfile                 # single image; compose overrides `command` per service
├─ run_local.ps1              # run everything against a native PostgreSQL, no Docker
├─ requirements.txt
├─ .env.example               # copy to .env
├─ alembic.ini, alembic/      # migrations own the schema (AUTO_CREATE_TABLES=false)
├─ docs/                      # BRD + HLD sources and built PDFs (make_pdf.py)
├─ packages/cp_common/        # shared lib: settings, db, security, auth, tenancy, MFA,
│                             #   SAML/OIDC, crypto, retention, observability, errors
└─ services/
   ├─ gateway/                # API gateway + admin console (static SPA)
   ├─ tenant_service/         # tenants, users, auth, MFA, SSO, sessions, onboarding
   ├─ branding_service/       # per-tenant branding + theme delivery
   ├─ config_service/         # per-tenant versioned detection configs
   ├─ ingestion_service/      # rail adapters, file/ISO 20022 intake, CBS events, queue
   ├─ analytics_service/      # metrics, detection runtime, RFA lifecycle, filings,
   │                          #   board packs, reference feeds, usage metering
   └─ notification_service/   # channels, preferences, deliveries
```

---

## 2. Run it

```bash
cp .env.example .env
docker compose up --build
```

Then open the console at **http://localhost:8080** and sign in with the seeded platform
admin (from `.env`): `admin@finverge.local` / `ChangeMe123!`. A demo tenant (`demo-bank`)
is provisioned on first boot so there's something to see.

Interactive API docs per service: `:8081/docs` through `:8085/docs`, and `:8087/docs`.

### 2b. Run locally against a native PostgreSQL (no Docker)

One-time setup:
```powershell
# 1) create the app role + database (as the postgres superuser)
#    (already provisioned on this machine: role 'cp', db 'controlplane')
#    psql -U postgres -c "CREATE ROLE cp LOGIN PASSWORD 'cp_password';"
#    psql -U postgres -c "CREATE DATABASE controlplane OWNER cp;"

# 2) create a project venv and install
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -e packages/cp_common
```

Apply the schema (Alembic owns it — `AUTO_CREATE_TABLES` is off):
```powershell
.\migrate.ps1
```

Run everything (starts all seven services against localhost:5432):
```powershell
.\run_local.ps1
```

Then seed multi-tenant test data through the gateway:
```powershell
.\.venv\Scripts\python scripts\api_seed.py
```

This creates three demo tenants (`demo-bank`, `hdfc-demo`, `icici-demo`), each with its own
branding and the default mule-layering typology, plus a `1.1.0` version on `hdfc-demo` to
show version activation. The schema itself comes from Alembic, not from startup —
`AUTO_CREATE_TABLES` is `false`, so run `.\migrate.ps1` first (see 6c).

---

## 3. API contracts (via the gateway, prefix `/api`)

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | public | Exchange email+password for a JWT |
| `GET  /api/tenants` | platform_admin | List tenants |
| `POST /api/tenants` | platform_admin | Onboard a tenant (provisions branding + default configs) |
| `GET  /api/tenants/{id}` | scoped | Get one tenant |
| `POST /api/tenants/{id}/suspend` | platform_admin | Suspend a tenant (blocks its admins from logging in) |
| `POST /api/tenants/{id}/resume` | platform_admin | Resume a suspended tenant |
| `POST /api/tenants/{id}/offboard` | platform_admin | Offboard a tenant (terminal; data retained + exportable) |
| `GET  /api/tenants/{id}/export` | platform_admin | Export the tenant's data bundle (RBI exit-clause) |
| `GET  /api/branding/{tid}` | scoped | Get branding |
| `PUT  /api/branding/{tid}` | scoped | Update branding (white-label) |
| `GET  /api/branding/{tid}/theme.css` | public | CSS custom properties for runtime theming |
| `GET  /api/branding/{tid}/theme.json` | public | Theme tokens as JSON |
| `GET  /api/configs/{tid}` | scoped | List a tenant's detection configs |
| `POST /api/configs/{tid}` | scoped | Add a new config version |
| `POST /api/configs/{tid}/{cid}/activate` | scoped | Activate a version (archives the previous) |
| `GET  /api/audit` | platform_admin | Read the audit trail (recent events) |
| `GET  /api/auth/password-policy` | public | The password rules the API enforces |
| `POST /api/auth/change-password` | any scope | Change your own password (accepts reset-scoped tokens) |
| `POST /api/tenants/{id}/suspend` | platform_admin | Suspend a tenant (blocks its admins from signing in) |
| `POST /api/tenants/{id}/resume` | platform_admin | Reactivate a suspended tenant |
| `POST /api/tenants/{id}/offboard` | platform_admin | Terminal offboard (irreversible) |
| `GET  /api/tenants/{id}/export` | platform_admin | Export the tenant's data bundle (exit clause) |
| `GET  /api/tenants/{id}/users` | scoped | List the tenant's users |
| `POST /api/tenants/{id}/users` | `can_admin_tenant` | Invite a user under one of the tenant's own roles (returns a one-time temp password) |
| `DELETE /api/tenants/{id}/users/{uid}` | `can_admin_tenant` | Remove a user (never the last administrator) |
| `GET  /api/auth/me` | any scope | Who you are and what your role may use: modules, dashboards, permissions, capabilities, description. The console renders its menu and Home page from this |
| `GET  /api/tenants/{id}/roles` | scoped | The tenant's roles, with permissions and member counts |
| `POST /api/tenants/{id}/roles` | `can_admin_tenant` | Create a role |
| `PUT  /api/tenants/{id}/roles/{name}` | `can_admin_tenant` | Edit a role. Taking something away applies at once; giving more is staged |
| `POST /api/tenants/{id}/roles/{name}/confirm` | `can_admin_tenant`, a *different* person | Confirm a staged privilege increase |
| `DELETE /api/tenants/{id}/roles/{name}` | `can_admin_tenant` | Delete a role (refused while held, or if it is the last way to administer the tenant) |
| `GET  /api/tenants/{id}/permissions` | scoped | What a role can be granted: gated actions, modules, dashboards |

"scoped" = valid JWT; platform staff are unrestricted, and tenant-scoped roles are limited to their own
tenant. Beyond that, who may do what is each role's own permissions (section 6d), never a fixed list of names.
Internal provisioning endpoints (`/internal/*`) are guarded by `X-Internal-Key`, not exposed
through the gateway.

---

## 4. Data model

The tables below are the control-plane core. They are **not** the whole schema — there are
around forty tables across eight schemas once ingestion, detection, cases, filings,
notification and metering are counted. Rather than duplicate them here and let the copy
drift, the authoritative map is `packages/cp_common/cp_common/schemas_db.py` (which schema
each service owns and may reach), with the per-schema table list in the HLD's data
architecture section.

| Table (owner service) | Key columns |
|---|---|
| `platform_users` (tenant) | id, email, password_hash, role, must_change_password, password_changed_at |
| `tenants` (tenant) | id, slug, legal_name, display_name, region, plan, status |
| `tenant_users` (tenant) | id, tenant_id, email, password_hash, role, must_change_password, password_changed_at |
| `tenant_roles` (tenant) | id, tenant_id, name, label, description, modules, dashboards, can_admin_tenant, can_reveal_pii, can_activate_config, permissions(json), source, pending_change — unique(tenant_id,name). **The only authority on what a role may do** |
| `branding` (branding) | tenant_id (pk), display_name, logo_url, primary/accent/neutral_color, default_theme, custom_domain |
| `tenant_configs` (config) | id, tenant_id, kind, name, version, status, body(jsonb) — unique(tenant_id,kind,name,version) |
| `audit_logs` (shared) | id, ts, service, actor, actor_role, tenant_id, action, target_type, target_id, status, detail(jsonb) |

---

## 5. Onboarding flow (orchestration)

```
POST /api/tenants
  └─ tenant-service: insert tenant(status=provisioning) + first tenant_admin  [commit]
       ├─ PUT  branding-service /internal/branding/{id}   (default theme)
       ├─ POST config-service   /internal/configs/seed    (mule-layering typology + rules)
       └─ set tenant.status = active   (or 'degraded' if a step failed → operator can retry)
```

The seeded detection pack is the **LAY (mule/layering) typology** — the same one from the
taxonomy work — so a new bank starts with a working baseline to calibrate.

---

## 5b. Tenant lifecycle

Tenants move through a small state machine, enforced server-side:

```
provisioning ─▶ active ⇄ suspended ─▶ offboarded (terminal)
                  │ (degraded on partial provisioning)
                  └────────────────────▶ offboarded
```

- **suspend** (active/degraded → suspended): the bank's admins are **blocked from logging in**
  (login returns 403); platform staff retain access.
- **resume** (suspended → active): restores tenant-admin login.
- **offboard** (→ offboarded, terminal): admins locked out, but **data is retained and exportable**.
- **export**: returns a JSON bundle of the tenant, its admins (no password hashes), branding, all
  config versions, and the audit trail — for the RBI outsourcing exit/data-return clause.

Invalid transitions (e.g. resuming an active tenant) return `409 invalid_transition`. All
transitions and exports are audited. Enforcement of suspension in the *data plane* (pausing the
tenant's live monitoring) is the data plane's job, reading this status.

The console surfaces these as Suspend / Resume / Offboard / Export buttons on the tenant detail
view, and the detection-config panel now supports creating new versions (with starter templates),
viewing a version's JSON, and activating a version (which archives the previous active one).

## 6. Branding / white-labelling

Each tenant's identity is stored as brand tokens and served two ways:
- `theme.json` — tokens for programmatic use,
- `theme.css` — ready-to-apply CSS custom properties (`--brand-primary`, `--brand-accent`, …).

Any tenant-facing surface fetches `theme.css` and applies it at runtime, so the same code
renders under each bank's identity. The admin console's branding editor shows a **live preview**.

---

## 6b. Rule editor, user management & tenant lifecycle

**Structured rule editor** — the console edits detection configs two ways, with live two-way sync:
a *Structured* mode (form rows for parameters, exit conditions and scoring bands) and a *JSON*
mode for paste/review. Load a starter via **Template**, edit either side, then save a new
version. Versions are immutable; **Activate** promotes one and auto-archives the previous.

**User management** — each tenant's administrators are listed per tenant. Inviting one
generates a **one-time temporary password** shown only in the response (never written to the
audit log); in production replace this with an emailed invite link. A tenant can never be left
with zero administrators — removing the last one returns `409`.

**Forced password reset** — an invited user's temporary password is single-use by design:

1. Logging in with it returns `must_change_password: true` and a **`password_reset`-scoped
   JWT** (15-minute lifetime).
2. That token is rejected by every endpoint except `/auth/change-password` — a temporary
   password can never reach tenant, branding, config or audit data (`403`).
3. A successful change clears the flag, stamps `password_changed_at`, and returns a fresh
   **full-scope** token so the user continues without re-logging in.

The console renders a dedicated reset screen (no cancel button when forced) and a voluntary
**Change password** action in the topbar. Policy — at least 12 characters with lower, upper,
digit and symbol; no common passwords; must not contain the user's email local-part — lives in
`cp_common/passwords.py` and is published at `GET /api/auth/password-policy` so the UI and API
can't drift. The seeded platform admin also starts with `must_change_password` set
(`SEED_ADMIN_MUST_CHANGE_PASSWORD=true`), since its password is a published default.

**Tenant lifecycle** — `active → suspended → active`, and a terminal `offboarded`:

| Action | From | To | Effect |
|---|---|---|---|
| suspend | active, degraded | suspended | Tenant admins are blocked at login (`403`) |
| resume | suspended | active | Access restored |
| offboard | active, degraded, suspended | offboarded | Terminal; login stays blocked |
| export | any | — | JSON bundle: tenant, users, branding, configs, audit |

Invalid transitions return `409`. The export bundle deliberately **omits password hashes** and
exists to satisfy the RBI outsourcing exit/data-return clause.

## 6c. Database migrations (Alembic)

**Alembic owns the schema.** `AUTO_CREATE_TABLES` defaults to `false` so the models and the
migration history cannot silently diverge — `create_all` only ever creates *missing* tables, it
never alters existing ones, which is how the `must_change_password` columns previously had to be
patched in by hand.

All commands go through the wrapper (it sets `PYTHONPATH` and `DATABASE_URL` for you):

```powershell
.\migrate.ps1                    # upgrade to head  (the common case)
.\migrate.ps1 current            # which revision is this DB on?
.\migrate.ps1 history            # list revisions
.\migrate.ps1 check              # do the models differ from the schema?
.\migrate.ps1 downgrade -1       # roll back one revision
.\migrate.ps1 revision --autogenerate -m "add x"
.\migrate.ps1 -Database cp_test upgrade head      # target another database
```

**Workflow when you change a model:** edit the model, run `.\migrate.ps1 revision
--autogenerate -m "what changed"`, **read the generated file** (autogenerate is a good first
draft, not an oracle — it misses column renames and server-side defaults), then `.\migrate.ps1`.
Commit the migration alongside the model change. Run `.\migrate.ps1 check` in CI to catch a
model edit that shipped without a migration.

**Baseline** — `f7d2d9a5d6ca` creates all six tables (`tenants`, `tenant_users`,
`platform_users`, `branding`, `tenant_configs`, `audit_logs`) with their indexes and
constraints. It was generated against an empty database and verified to produce a schema
byte-identical to the live one (52 columns, 17 indexes), with `upgrade`/`downgrade` both
exercised. The existing database was then marked as already at this revision via
`alembic stamp head`, so no data was touched.

**Adopting an existing database** (one built by `create_all`): confirm the schema matches the
baseline, then `.\migrate.ps1 stamp head` — this records the revision without re-running the
DDL. Running a plain `upgrade` there would fail on tables that already exist.

## 6d. Roles are data: modules, dashboards, permissions

**What a role may do is stored in the database, per tenant, and edited in the console. No service
decides by role name, and there is no list of roles in code to fall back on.**

Each tenant has its own rows in `tenant.tenant_roles`. A role holds five things:

| | |
|---|---|
| Modules | Control Plane, Monitoring, Audit (the platform-only Administration module can never be granted to a tenant role) |
| Dashboards | which of the persona dashboards it opens; the **first** is its home dashboard |
| Capabilities | `can_admin_tenant`, `can_reveal_pii`, `can_activate_config` |
| Permissions | the gated actions it may perform (below) |
| Description | shown to its holders on their home page |

**Starter roles.** Onboarding copies ten starter roles (`tenant_admin`, `analyst`, `investigator`,
`risk_manager`, `principal_officer`, `supervisor`, `board`, `cro`, `data_scientist`, `rbi_inspector`)
into the tenant's own rows; a data migration did the same for tenants that already existed. They
reproduce the previous behaviour exactly, so a tenant behaves as before until it edits one. After the
copy they are ordinary rows: a tenant administrator, or platform staff acting for the tenant, can add a
role, narrow or widen any role, or delete one from the **Roles** page. The templates live in
`packages/cp_common/cp_common/rbac.py` (`ROLES`) and are used for seeding only.

| Starter role | Modules | Dashboards (home first) |
|---|---|---|
| `tenant_admin` | Control Plane, Monitoring, Audit | all twelve tenant dashboards |
| `analyst` | Monitoring | Analyst, EWS Signals, Real-Time |
| `investigator` | Monitoring | Investigator, Account 360, RFA Lifecycle, EWS Signals, Analyst |
| `risk_manager` | Monitoring, Audit | Operations, EWS Signals, Real-Time, Analyst, Investigator, RFA, Board |
| `principal_officer` | Monitoring, Audit | AML/STR, RFA Lifecycle, Compliance |
| `supervisor` | Monitoring, Audit | Compliance, RFA, AML/STR, EWS Signals, Board |
| `board` | Monitoring | Board |
| `cro` | Monitoring, Audit | Board, Compliance, RFA, Operations, AML/STR, EWS Signals |
| `data_scientist` | Monitoring | Model Risk, EWS Signals, Analyst |
| `rbi_inspector` | Monitoring, Audit | Inspection, RFA, Compliance, Board, AML/STR, Model Risk, EWS Signals, Account 360 |

(Platform staff, `platform_admin`, are the one role that is not a row: it is recognised by name because
it is not a bank's role, works across tenants and cannot be edited by any tenant. It also gets the
Tenant Health dashboard, which no tenant role can be granted.)

**Permissions** are the gated actions the platform can guard: the case-workflow steps (one each,
`case.act.<action>`), assigning cases, running and concluding the staff-accountability examination,
recording recoveries, simulating and replaying detection, loading reference lists, filing FMR/STR and
CTR returns, preparing and issuing board packs, viewing usage, issuing machine credentials, **sanctions
screening** (`sanctions.screen`) and **borrower credit health** (`lane_c.view`, `lane_c.manage`).
The catalogue is `packages/cp_common/cp_common/permissions.py` (served by `GET /api/tenants/{id}/permissions`);
who holds each one is the role's data. There is no implicit "administrator may do anything": a role holds
each permission explicitly. Board, CRO and Model Risk hold neither sanctions nor credit-health
permissions by default; Analyst, Investigator, Supervisor and RBI Inspector can look but not change.

**How a change behaves.**

- Taking something away (an action, a module, a dashboard, a capability) applies on the person's next request.
- Giving a role more (a capability or a permission) is staged and needs a *different* administrator to
  confirm it; until then the role is as it was.
- An edit or delete that would leave the tenant with no administrator who can sign in is refused, as is
  deleting a role someone still holds.
- Unknown permissions, the Administration module and Tenant Health are refused.
- Every create, edit, confirmation and delete writes a full before/after snapshot to the audit trail.

**How it is enforced.** tenant-service reads a tenant's rows directly; every other service that
authorises a request (analytics, config, notification, decision review, Lane C) fetches them from
tenant-service through `cp_common.dynamic_roles` and caches them against a shared generation, so an edit
is live everywhere on the next request. A role name that is not a row for the tenant can do nothing; if
tenant-service cannot be reached and a service has no cached copy, it refuses rather than guesses.
Endpoints ask one question, `has_permission(tenant_id, role, "…")`. The console renders its menu, its
Monitoring tabs and its **Home** page from `GET /api/auth/me`, so a role is shown only what it may open;
hiding a page is not the control, the endpoint behind it re-checks.

**Adding a gated action.** Add it to `permissions.py`, guard the endpoint with `has_permission`, add the
starter grant to `rbac.py` and write a new Alembic data migration that grants it to existing tenants'
starter roles (never edit an applied migration; never overwrite a tenant's own edits). See
`alembic/versions/f3c6d9e2a5b8_screening_and_credit_health_permissions.py`.

Dashboard access is checked the same way: every analytics endpoint calls `_guard()`, which checks tenant
scope, module access and dashboard access from the tenant's role rows. An `analyst` gets `403` on the
board and supervisor dashboards unless its tenant has granted them.

## 6e. Monitoring dashboards & reconciliation

Thirteen persona dashboards over an analytical store of `fact_transaction` → `fact_alert` →
`fact_case`, filterable by period, rail, EWS family, severity and region, with
filter-preserving drill-down to alert / case / transaction rows.

All eleven dashboards from the product spec's catalogue, plus two beyond it.

| Spec | Priority | Dashboard | Persona | Answers |
|---|---|---|---|---|
| D-03 | P1 | Analyst | L1 Fraud Analyst | What do I work next, and is it real? |
| D-02 | P1 | EWS Signals | Fraud Risk Manager / Analyst | Which indicators fire — and which are silent? |
| D-04 | P1 | RFA Lifecycle | Investigator / Compliance | Where is each red-flagged account? |
| D-01 | P1 | Board | CRO / ACB / Special Committee | What is our exposure and are we compliant? |
| D-05 | P1 | Compliance | Compliance Supervisor | Are the regulatory clocks being met? |
| D-06 | P2 | Real-Time | Operations | What is flowing, and what are we stopping? |
| D-07 | P2 | AML / STR | Principal Officer (PMLA) | What must be filed with FIU-IND, by when? |
| D-08 | P2 | Account 360 | Senior Investigator | Everything known about one account. |
| D-09 | P3 | Investigator | L2 Senior Investigator | Is this one mule, or a ring? |
| D-10 | P3 | Model Risk | Model Risk / Data Science | Is the model still fit, fair and explainable? |
| D-11 | P3 | Tenant Health | Platform Administrator | Which bank's pipeline is degraded? |
| — | — | Operations | Fraud Risk Manager | Where is the queue failing, and why? |
| — | — | Inspection | Internal Audit / RBI | Prove why this account was flagged. |

**Tenant Health is platform-staff only** — it carries operational telemetry and returns no
customer rows at all. No tenant role can be granted it (role create/edit refuses it), so every tenant-scoped role gets `403`.

**Account 360** pins every figure to one account via `?account=AC…` (matching either side
of the transaction); without it the dashboard shows the population so an investigator can
pick a row.

### Filter framework

24 filter dimensions, all exposed in the UI. A compact row carries the common ones; a
**More filters** panel holds the rest so the toolbar stays readable.

| Group | Filters |
|---|---|
| Time | preset (today / 7d / 30d / MTD / QTD / YTD) or custom range, **+ compare to previous period** |
| Entity | tenant, region, branch |
| Rail | UPI, IMPS, NEFT, RTGS, cards |
| Product | savings, current, loan, credit card, trade finance |
| Customer | segment, account (either side of the transaction) |
| Signal | EWS family, rule ID, typology, severity, disposition, config version |
| Case | lifecycle state, assignee, FMR category, RFA-only |
| Amount | min / max (entered in rupees, sent as paise) |
| Regulatory | FMR status, STR status (filed / due / overdue), natural-justice breach only |

Rule, typology and assignee options are **populated from the tenant's own data** rather
than a hard-coded list, so they never drift from reality.

**Sticky, shareable, savable.** Filters survive every drill-down stage; the active set is
written to the URL hash, so **Copy link** shares the exact view (filters + dashboard +
tenant) and a recipient lands on it. **Save view** names a filter set for reuse — stored
server-side in `saved_views` and shareable with the team (see 6j).

**Comparison period** refetches the same dashboard over the immediately preceding window of
equal length and shows a delta per KPI. It requires an explicit range ("previous" is
undefined without one) and is deliberately suppressed for volatile metrics.

### PII masking & audit-of-view (DPDP)

Customer identifiers — account numbers, device ids, IP addresses — are **masked
server-side by default**. Masking in the browser would be theatre: the clear value would
already have crossed the network. `AC1747162981` leaves the process as `AC••••••2981`.

Unmasking requires **all three**:

1. the role holds `can_reveal_pii`,
2. the caller asks explicitly (`?reveal=true`),
3. a justification of at least 8 characters.

Anything less returns masked data. A successful reveal writes `data.reveal_pii` to the
audit log with the justification, and every evidence view writes `data.view_evidence`
recording which PII fields the payload carried — so the trail shows who *looked*, not only
who changed something.

Starter grants (`can_reveal_pii`; a tenant's administrator can change it per role):

| Role | May unmask |
|---|---|
| tenant_admin, analyst, investigator, risk_manager, principal_officer, supervisor, rbi_inspector | yes |
| board, cro, data_scientist | no — aggregate and model work run on masked data |
| **platform_admin** | **no** |

`platform_admin` is deliberately excluded: operating the service does not require seeing a
bank's customers, and this is precisely what an outsourcing review probes.

### Trend / time series

`GET /analytics/{tid}/timeseries?metric=…&bucket=day|week|month` buckets any registry
metric over time using its own fact timestamp, so a trend line is the *same definition* as
the headline number — the bucket totals sum exactly to it. Each dashboard shows a trend
chart for its most representative metric; the bucket is switchable.

### Volatile metrics

Seven metrics depend on wall-clock `NOW()` — ingestion lag, backlog age, the overdue and
stalled counts. They are flagged `volatile: true` in the API because they are **point-in-time
readings**: they change between calls, so they cannot reconcile across dashboards and cannot
be reproduced "as of" a past date. Check **R-10b** enforces that no figure backing a
regulatory filing is ever volatile.

**Figures reconcile by construction.** Every dashboard number is looked up by name from
the single registry in `services/analytics_service/app/metrics.py` — there is exactly one
SQL definition per metric, so the board's "total fraud value" and the supervisor's are
*the same query*, not two that happen to agree. Route handlers contain no SQL.

`reconciliation.py` then proves it with 16 invariants, surfaced live on the supervisor
dashboard: referential integrity (R-01/02), case value equals the sum of its linked
transactions (R-03), board headline equals case detail (R-04), FMR filings tie to
classified cases (R-05/05b), exact partitioning of every breakdown (R-06/07), derived
metric arithmetic (R-08), and metric determinism (R-10).

Money is stored as **integer paise** end to end. Floating-point money does not add up, and
`Decimal` results are normalised back to `int` so exact equality is meaningful.

To prove the checks actually work, corrupt some data deliberately:
```powershell
.\.venv\Scripts\python scripts\generate_synthetic_data.py --tenant hdfc-demo --inject-breaks
```
R-01, R-03 and R-09 then fail with exact deltas; regenerate with `--reset` to clear.

### Charts and the 6-stage drill

Charts are **Apache ECharts 5.5** (Apache-2.0), vendored at
`services/gateway/app/static/vendor/echarts.min.js` rather than loaded from a CDN, so the
console works on an air-gapped bank server. The categorical palette derives from the
tenant's brand primary, so charts inherit white-label branding.

Every chart segment is clickable and advances one stage:

| # | Stage | What it shows | How you advance |
|---|---|---|---|
| 1 | Portfolio | Whole book | Click a rail / region / product |
| 2 | Segment | Sliced by rail, region, product | Click an EWS family, rule or severity |
| 3 | Signal | Which indicator fired | Click a lifecycle state or FMR category |
| 4 | Case | Cases and their state | — |
| 5 | Records | Alert / case / transaction rows | Click a row |
| 6 | Evidence | One record + its trail | — |

A drill only ever **adds** a filter, so a figure at any stage is the same metric
definition through a narrower lens. The breadcrumb shows the filters pinned at each
stage and clicking back peels them off. Stage 6 renders the record, its transaction, its
case, sibling alerts, and the **config version in force when the score was produced** —
plus, for a case, a live R-03 check that its stated value equals the sum of its linked
transactions.

**Analytical store** is PostgreSQL behind the `QueryEngine` interface
(`app/engine/base.py`). A ClickHouse engine implements the same three methods and is
selected with `ANALYTICS_ENGINE=clickhouse` — no dashboard or metric changes.

### Synthetic data

Two properties matter about how it is generated.

**It is internally consistent.** Case values are *derived* by summing the linked
transactions, never invented — a generator that fabricated totals would make the
reconciliation suite pass vacuously.

**It has real structure.** Transactions draw from a fixed 1,400-account pool so accounts
recur and relationships exist. On top of that baseline the generator injects genuine
typologies, and the LAY/TBM alerts attach to *those specific* transactions:

| Injected | What it creates |
|---|---|
| Fan-in / fan-out | A mule hub with 8–16 sources paying in and 6–12 sinks paid out within the hour, sharing a device |
| Circular flow | A → B → C → A returning value to origin within a day |
| Pass-through | Credit in, near-identical debit out within minutes |
| Trade-based | High-value RTGS trade-finance flows (TBM was previously almost unrepresented) |
| Fraud burst | A concentrated 3-day attack window — real fraud is bursty, and uniform noise makes the trend line flat |

Each injected structure becomes **one case**, so drilling a single alert reveals the whole
ring. Without this the layering alerts are decoration: the Investigator asks "is this a
ring?" and the data has no rings in it.

```powershell
.\.venv\Scripts\python scripts\generate_synthetic_data.py --reset          # rebuild
.\.venv\Scripts\python scripts\generate_synthetic_data.py --append --minutes 30
```

`--append` adds traffic ending *now*, so the Real-Time view moves and ingestion lag falls —
run it on a schedule for a live-feeling demo.

### Demo data & users
```powershell
.\.venv\Scripts\python scripts\generate_synthetic_data.py --reset --days 60 --txns 9000
.\.venv\Scripts\python scripts\seed_demo_users.py --tenant hdfc-demo
```
The user seeder goes through the real invite flow (temporary password → forced reset),
then completes the reset, so the feature is exercised rather than bypassed.

## 6f. Tests & CI

```powershell
.\.venv\Scripts\python -m pytest -q          # about 1,520 tests; a full run takes roughly half an hour
.\.venv\Scripts\python tools\jscheck.py      # static assets
```

Tests run against a dedicated `cp_test` database that is created, **migrated through
Alembic** and dropped by the fixtures — so the migrations are exercised too, not just the
models. Dev data is never touched. Every session drops and recreates its test database, so set
`TEST_DB_NAME` to a name of your own whenever anyone else may be running tests on the same server.
A test tenant needs role rows (`seed_default_roles`), because roles are the only authority.

| Suite | Covers |
|---|---|
| `test_static_assets` | Structural check of first-party JavaScript |
| `test_rbac` | Starter role matrix, module/dashboard access, tenant isolation, platform PII boundary |
| `test_roles_are_data` | An edit to any role, starter roles included, is obeyed on the next request; staged elevation; last-administrator guard; platform admin may edit any tenant |
| `test_screening_permissions` | Sanctions and Lane C are refused without the permission; every Lane C route is guarded |
| `test_auth_me` | What the console renders from, including custom roles |
| `test_privacy` | Masking, reveal gating, audit-of-view, graph pseudonym uniqueness |
| `test_reconciliation` | The 16 invariants under filters — **including proof they fail on corrupted data** |
| `test_analytics` | Dashboards, registry identity, filters, trend additivity, drill, graph |

Two of these deserve note. `test_breaks_are_detected` deliberately corrupts three rows and
asserts exactly R-01, R-03 and R-09 fire — a check suite that only ever passes is
indistinguishable from one that does nothing. And `tools/jscheck.py` exists because a
string literal broken across a newline once invalidated the whole `app.js`, so the console
rendered nothing and only the browser noticed; there is a test asserting the checker
catches that exact defect.

There is no CI workflow in the repository today: run the suite, `tools/jscheck.py`, `tools/htmlcheck.py` and
`.\migrate.ps1 check` (it catches a model edit that shipped without a migration) before every commit.

Console assets are served `no-cache, must-revalidate` (the vendored chart library stays
immutable), so a deploy no longer needs a manual hard refresh.

## 6g. Rule-evaluation trace & link analysis

**Trace.** Every alert records the sub-rule band that matched, the human reason, and the
observation against its threshold — pinned to the config version in force at scoring time.
Stage 6 leads with it:

> **CPT-03 `.02`** — Same collateral charged to multiple lenders
> observed **4** vs threshold 2 `lender_count`
> evaluated under config version **1.0.0**

A score alone cannot answer "why was this flagged"; this can, years later, against the
rules that actually ran rather than today's.

**Graph.** `GET /analytics/{tid}/graph?case_id=…|account=…` returns the account network,
with edges aggregated per account pair (a hub with 200 payments reads as one thick edge per
counterparty, not 200 hairlines). Nodes are classified `hub` / `collector` / `disburser`,
sized by counterparty count, and rendered as an ECharts force graph on the Investigator,
Account 360 and AML dashboards.

Node ids are account numbers, so the same masking applies. Because masking keeps only a
prefix and the last four digits, two accounts can collapse to one label — harmless in a
table, but in a graph it would merge two real accounts into a single node. The route
therefore assigns **stable, unique pseudonyms**, with a test asserting no collisions.

## 6h. Control plane drives detection

The control plane is authoritative for three things per tenant, and detection reads all
three at run time:

| What | Where | Effect |
|---|---|---|
| **EWS rule catalogue** (28 rules, 9 families) | `config_service/app/ews_catalogue.py` | Thresholds decide what fires |
| **FRM policy** (clocks, floors, severity, governance) | `config_service/app/policy.py` | Drives metrics, clocks and dashboards |
| **Typology configs** | seeded per tenant | Scoring combinations |

This used to be broken in a way worth remembering: config stored rule `018@1.0.0` while
detection emitted `LAY-02`, so retuning a threshold in the console changed nothing at all.

**Observations are drawn from a range fixed per rule, independent of the threshold.** If
the range scaled with the threshold, retuning would be a no-op — the whole point is that
it is not. Demonstrated on `CHN-02` over identical traffic:

| Configured threshold | Alerts fired |
|---|---|
| 200 | 49 |
| 900 | 43 |
| 4800 | **3** |

Injected typologies present a *strong* observation but are **not** force-fired past the
threshold: a tenant that sets a threshold too high genuinely misses real fraud, and hiding
that would make misconfiguration invisible.

### Entity type drives policy

RBI issued the July 2024 directions separately for commercial banks/AIFIs, for
co-operative banks (UCBs/StCBs/CCBs) and for NBFCs/HFCs, so a single hard-coded SLA cannot
serve all of them. `entity_type` (plus `ucb_tier`) is set at onboarding and selects the
defaults:

| Tenant | Entity | Reports to | SLA | LEA floor | Governance |
|---|---|---|---|---|---|
| hdfc-demo | Commercial Bank / AIFI | RBI, FIU-IND | 24h | ₹1 Cr | SCBMF |
| icici-demo | Urban Co-op (Tier 2) | RBI, FIU-IND | 48h | ₹10 L | Board of Management |
| demo-bank | State Co-op (StCB) | RBI, **NABARD**, FIU-IND | 48h | ₹10 L | Board of Management |

Metric SQL carries policy placeholders next to the table alias, so one board-approved
number drives detection, the clocks and the dashboards:

```
{b}.ts < NOW() - make_interval(hours => {sla_breach_hours})
```

**Every numeric value is a placeholder.** They are shaped correctly and differ sensibly by
entity type, but each must be set from that entity's board-approved FRM policy and
validated against the live Master Direction. The config body says so itself (`_note`).

## 6i. Pagination & export

Drill views were capped at 40 rows with no total, so the first page silently looked like
the whole result set. They now report `total`, `limit`, `offset` and `has_more`, with page
sizes of 25–250 and a hard cap of 1,000 per request.

**Export** (`GET /analytics/{tid}/export/{entity}`) returns filtered rows as CSV that is:

* **masked by default** — unmasking needs the capability, a justification, and is audited;
* **watermarked** — the file carries who exported it, when, for which tenant, under which
  filters, and whether customer data was unmasked, so a spreadsheet found later is
  traceable and reproducible;
* **row-capped** at 50,000 — a bulk extract of a whole tenant is not a reporting feature;
* **audited** as `data.export`.

## 6j. Live feed, saved views, model drift

### Live feed

Real-Time would otherwise show a frozen snapshot whose ingestion lag only grows when no
bank feed is connected. `scripts/live_feed.py` appends traffic on an interval, **scored
against each tenant's configured rules** — the same path as the historical load, so a
retuned threshold applies to arriving traffic too.

```powershell
.\.venv\Scripts\python scripts\live_feed.py --interval 30 --rate 60
.\.venv\Scripts\python scripts\live_feed.py --once        # for an external scheduler
```

Each tenant commits separately: one failing feed must not roll back the others. It is a
demo aid — when real ingestion exists, this is deleted.

### Saved views

Views were browser-local: lost on a cache clear, invisible on another machine, impossible
to hand to a colleague. They now live in `saved_views`, scoped to the tenant and owned by
a person, with the filter set stored as the same query string the URL carries — so a saved
view and a shared link are interchangeable.

* **Private by default**; "Share with my team" makes a view visible tenant-wide.
* A shared view is still its owner's: only they, or a tenant admin, may delete it.
* Saving the same name overwrites rather than duplicating.
* `view.save` / `view.delete` are audited.

### Model drift (FREE-AI)

Precision alone cannot show that the population a model scores has moved underneath it —
a model can hold its hit-rate while the traffic changes shape, and the first visible
symptom is a quiet collapse months later. `GET /analytics/{tid}/drift` compares the
current window against the preceding one of equal length:

| Measure | What it catches |
|---|---|
| **PSI** | How far the score distribution's *shape* has moved. `< 0.10` stable, `0.10–0.25` moderate, `> 0.25` significant |
| **KS** | The largest gap between the two cumulative distributions — sensitive to a shift concentrated in one part of the range, which PSI can average away |
| **Mix shift** | Which rules gained or lost share — PSI says the population moved, this says where to look |

Both are computed over bins taken from the **reference** window, then applied to the
current one; re-binning each separately would compare two different rulers and hide the
movement being measured. Empty bins use a small epsilon rather than returning infinity.

## 6k. Case evidence, model governance, scheduled distribution

### Case documents - the natural-justice trail

A show-cause timestamp is not evidence that a reasoned order exists. `case_documents`
stores the artefacts themselves: show-cause notice, borrower response, **reasoned order**,
LEA referral, FMR filing.

* **No delete endpoint.** A compliance trail that can be quietly pruned is not a trail;
  superseding means uploading a newer document and both remain.
* **SHA-256 on every upload**, returned as `X-Content-SHA256` on download, so a document
  produced during an inspection can be shown to be the one that was filed.
* Upload *and retrieval* are audited - looking at case evidence is itself an event.

The metric that makes this matter is `fraud_without_reasoned_order`: cases classified as
fraud with no reasoned order on file. It sits on the RFA Lifecycle dashboard, and attaching
an order visibly closes the gap.

### Model governance (FREE-AI)

| Endpoint | Answers |
|---|---|
| `GET /analytics/{tid}/model-inventory` | What is actually scoring traffic, and did the control plane approve it? |
| `GET /analytics/{tid}/overrides` | Where did a human disagree with the model? |

The inventory is **derived from what ran**, not from a maintained list - a spreadsheet
inventory drifts from production and the drift is the risk. A config version scoring live
traffic that is not ACTIVE in the control plane is flagged as `governance_gap`.

The override log counts both directions: a strong signal dismissed as a false positive,
and a weak signal escalated. Neither is wrong - that is what human accountability means -
but a rising override rate says the model and its users have diverged, which precision
alone hides.

### Scheduled distribution

A board pack that only exists when someone remembers to export it is not a governance
control. Subscriptions store a dataset plus the same filter query a saved view holds, on a
daily / weekly / monthly cadence.

```powershell
.\.venv\Scripts\python scripts\run_subscriptions.py --once
```

**Delivery is pluggable and honest about what is wired.** Rendering is ours; delivering
needs a mail relay this deployment does not have. The `spool` driver writes the artefact to
disk; the `email` driver records `NOT SENT - no mail relay configured` and retains the file.
It never claims to have sent a report that went nowhere, because a governance control that
silently fails is worse than one that is visibly absent. Every run is audited.

## 7. Audit logging & error handling

Every meaningful mutation is written to an append-only `audit_logs` table with actor, role,
tenant, action, target, status and a JSON detail. Audited actions: `auth.login`
(success/failure), `tenant.onboard`, `tenant.suspend`, `tenant.resume`, `tenant.offboard`,
`tenant.export`, `user.invite`, `user.remove`, `auth.change_password` (success/failure with
reason, never the password itself), `branding.update`, `config.create`, `config.activate`.

- Audit writes use their **own short-lived DB session** and are **best-effort**: a failure to
  write an audit record is logged locally and never propagates into (or rolls back) the
  business operation being audited — see `cp_common/audit.py`.
- `cp_common/errors.py` installs two handlers: `AppError` → a typed JSON error with the right
  status code, and a catch-all `Exception` handler that logs the full traceback and returns a
  generic `500` so internal details never leak to clients.
- The console's **Activity** panel reads `/api/audit` (platform staff only).

## 8. Security notes (before production)

- Change `JWT_SECRET`, `INTERNAL_API_KEY`, `CP_ENCRYPTION_KEYS` and all seed passwords.
  The key in `.env` is a development value and is published in this repository's history —
  generate a fresh one and re-seal with `scripts/seal_secrets.py`.
- Schema is already Alembic-managed (`AUTO_CREATE_TABLES=false`) — see section 6c. Run
  `.\migrate.ps1` as a deploy step, and `.\migrate.ps1 check` in CI.
- Review each tenant's roles before go-live (Roles page): the starter grants are a starting point, and
  every cell is the tenant's own data. Every authorising service must reach tenant-service for role lookups
  (`TENANT_SERVICE_URL`, `INTERNAL_API_KEY`); run it on at least two instances.
- Provision the per-service database roles (`scripts/provision_db_roles.py`). Running every
  service as one superuser discards the isolation the schema split exists to enforce.
- Put the gateway behind TLS. Rate limiting is in-process, so with N replicas the effective
  ceiling is N× — it is a safety valve against runaway clients, not a billing control.
- **Enable volume encryption.** Application-layer sealing covers MFA seeds, IdP secrets and
  feed credentials; it does not protect a stolen disk or a discarded backup. Postgres TDE,
  LUKS or the cloud provider's volume encryption is still required (BRD BR-712).
- Review the retention floors against the entity's own policy before go-live
  (`packages/cp_common/cp_common/retention.py`), and schedule `scripts/run_retention.py`.
- For RBI data-localization, pin all infra to in-India regions.
- Still outstanding before production: distributed tracing and log shipping, a tested
  backup/restore and DR runbook, and load-test evidence for the latency NFRs. See
  "What still blocks a commercial release" in the BRD.
```
