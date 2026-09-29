# ISO 8583 Front Door for Lane A — Technical Scoping Document

**Status: BR-317, Partial.** Core protocol adapter (codec, DE mapper, TCP connection
handler, decision-client) is built and unit-tested — 45 tests covering message round-trip,
PAN masking, DE7's year-less date resolution, and every gateway failure path producing an
explicit refusal rather than a guessed approval. Code: `services/iso8583_gateway/`. Tests:
`tests/test_iso8583_codec.py`, `tests/test_iso8583_mapper.py`, `tests/test_iso8583_server.py`.
Documented in `brd.html` (BR-317), `fsd.html` and `hld.html`.

**Not built, and not guessable — see §7 below, unchanged by this update:** vendor-specific
private-field mapping (DE48/62/120–127), a confirmed dialect against any real pilot switch,
the PCI-DSS scope decision in §5, and certification testing. **Not deployed for any
tenant.** This document's original scope and open questions (§3–§9) remain the plan; only
this status line and §10 below (new) have changed.

## 1. Problem Statement

Lane A's real-time decision endpoint (`POST /decide`, `decision_service`) speaks JSON only.
Several payment-switch integrations — principally card-side authorization switches, and
some banks' own EFT/ATM switches — speak **ISO 8583** natively and have no practical way to
originate a JSON call from the authorization host itself. Requiring every such vendor to
build a JSON translation layer on their side is not realistic: it is a bespoke integration
project per vendor, on infrastructure Fraud360 does not control, for a protocol conversion
that is well-understood and should only need to be built once.

**Goal:** Fraud360 exposes a second, native ISO 8583 front door onto the same decision
core that `/decide` already uses, so a switch that only speaks 8583 can integrate without
any protocol work on its side.

**Non-goals (this scoping round):**
- Not replacing the JSON `/decide` contract — it stays, for switches that already speak it.
- Not covering settlement/batch file formats (that gap, if any, is separate).
- Not solving Lane B/CBS integration — out of scope, unaffected.

## 2. Why This Can't Just Be "ask the vendor"

- Card/EFT switches are frequently on-prem, vendor-licensed (FSS, ACI, Euronet, legacy
  FIS/TSYS stacks) with no REST layer available without a vendor change request, a release
  cycle, and often a fee — per bank, repeated for every engagement.
- It inverts risk the wrong way: a hand-rolled translator built once per vendor, with no
  shared test suite, fails silently in a live authorization path Fraud360 cannot observe or
  debug.
- It's the industry-standard shape to get this backwards from how it's normally solved —
  authorization-time risk engines (Falcon, ReD Shield, and similar) expose native 8583
  listeners for exactly this reason: the translator belongs on the vendor-agnostic side,
  built once, tested once.

## 3. Proposed Architecture

```
Switch (ISO 8583, MTI 0100/0200, persistent TCP socket)
        │
        ▼
  NEW COMPONENT: iso8583_gateway
   - TCP listener, one persistent connection per switch (or connection pool)
   - Parses message length header + bitmap + data elements
   - Maps DE fields → the existing canonical DecideIn shape
   - Calls decision_service's internal decide() — same rule catalogue,
     same policy, same budget_ms concept — NOT a second decision engine
   - Formats the response back as MTI 0110/0210 with a mapped response code
        │
        ▼
  Switch gets an ISO 8583 response inside its own authorization window
```

**Key design decision: this is a protocol adapter, not a second decision path.** It calls
the same internal decision function `decision_service` already exposes over HTTP — same
rule catalogue, same per-rail policy (budget_ms, fail-open/closed, shadow mode), same
decision log. Two front doors, one brain. This avoids the two lanes silently drifting on
what "decline" means, the way the platform already avoids it between Lane A and Lane B
(`cp_common.observations.observe()` is shared for the same reason).

**Deployment**: its own deployable, mirroring why `decision_service` is already separate
from everything else — a raw-socket listener has a different failure mode and scaling
profile than an HTTP service behind the gateway, and a hung 8583 connection must not be
able to affect the JSON path or vice versa.

## 4. Field Mapping (starting point — needs vendor confirmation, see §7)

| ISO 8583 DE | Field | Maps to canonical | Notes |
|---|---|---|---|
| MTI | Message type | request/response class | 0100/0200 = auth request; 0110/0210 = response |
| DE2 (PAN) / DE35 (track2) | Card number | `debtor_account` | **Must not carry raw PAN — see §5** |
| DE3 | Processing code | `channel`, transaction sub-type | |
| DE4 | Amount, transaction | `amount_paise` | 8583 amounts are unsigned, implied decimal — needs the same `Decimal`-exact conversion `to_paise()` already does, not float |
| DE7 | Transmission date/time | `ts` | 8583 carries no timezone — bank's local convention must be pinned explicitly, not assumed (same discipline as `to_utc()` refusing naive timestamps today) |
| DE11 | STAN (system trace audit number) | `txn_ref` (part of) | |
| DE37 | RRN | `txn_ref` | Prefer RRN where present — more globally unique than STAN alone |
| DE41 | Terminal id | `device_id` | |
| DE42 | Card acceptor id (merchant) | `creditor_account` | |
| DE43 | Card acceptor name/location | attributes / signal context | |
| DE39 | Response code | `action` → response code | Output direction: `allow`→`00`, `decline`→`05`, `challenge`→ referral (`01`) if the network supports it, else treated as decline |
| DE48 / DE62 / DE120-127 | Private/proprietary fields | — | **Vendor-specific.** Every switch vendor uses these differently; cannot be finalized generically — see §7 |

## 5. Compliance Flag — this is the decision that blocks everything else

DE2/DE35 in a raw ISO 8583 authorization message carry the **actual PAN / track 2 data**.
The existing JSON contract deliberately never accepts this — `pan_token` is documented as
"a tokenised PAN reference, never a raw card number" (Appendix A.3 of the integration
spec). An ISO 8583 listener sitting in front of Fraud360 would, by construction, receive
raw PAN on the wire.

**This has to be resolved before implementation starts, not during it:**
- Where does detokenization/tokenization happen — is Fraud360 in-scope for PCI-DSS as a
  result, or does the switch/HSM layer tokenize before the message reaches Fraud360?
- If Fraud360 must terminate the raw 8583 socket itself, the `iso8583_gateway` component
  becomes part of the cardholder-data environment and needs the corresponding controls
  (network segmentation, encryption in transit even though 8583 sockets are traditionally
  plaintext/leased-line, key management, audit scope) — a materially bigger build than the
  protocol parsing alone.
- Recommended default position: **never persist or log raw PAN**, mask/hash immediately at
  the parse boundary before anything downstream sees it — mirrors how the JSON adapters
  already refuse to accept anything but a token.

This should go to the bank's compliance/security team as an explicit design question
before any code is written.

## 6. Latency and Failure Semantics

- The listener adds a parse/map hop in front of the same `decide()` call the JSON path
  uses. Budget math should stay inside the rail's existing `budget_ms` envelope — no new
  latency class is being invented, just a second entry point into the same one.
- Fail-open/closed, shadow mode, and the decision log all reuse what's already built —
  nothing new to design there.
- Response code mapping (DE39) needs to be finalized per-network — ISO 8583 response codes
  are not perfectly standardized across card networks; RuPay/NPCI, Visa and Mastercard each
  have documented but slightly different code tables.

## 7. Open Questions Requiring a Decision Before Scoping Firms Up

1. **Which ISO 8583 variant/dialect?** 1987 vs 1993 vs 2003, and which network's private
   field usage (Visa Base I, Mastercard, RuPay/NPCI) — this is not one spec, it's a family,
   and every vendor has proprietary deviations in the private field ranges (DE48, DE62,
   DE120–127). Cannot be finalized without naming the first pilot switch/bank.
2. **Transport**: raw TCP socket (traditional, often over a leased line or VPN) vs an
   MQ-fronted variant some newer switches offer? A queue-fronted approach doesn't fit a
   sub-200ms authorization budget unless the queue hop is negligible — needs confirming per
   vendor.
3. **PCI scope** — see §5. This is the one that can change the size of the whole effort.
4. **Message framing** — 2-byte vs 4-byte length header, ASCII vs BCD field encoding: both
   vary by vendor and must be confirmed, not assumed.
5. **Certification/conformance testing** — card networks and many switch vendors require a
   certification pass before going live on 8583; this adds calendar time independent of
   Fraud360's own build effort and should be planned for explicitly.
6. **Pilot partner** — who is the first bank/switch this gets built and tested against?
   Everything in §4 and §7.1–7.4 should be confirmed against that vendor's actual spec
   document rather than the generic ISO 8583 standard, because in practice no two switches
   implement it identically.

## 8. Phased Effort Estimate (rough — firms up once §7 is answered)

| Phase | Work | Rough effort |
|---|---|---|
| 0. Spec confirmation | Get the pilot vendor's actual 8583 spec doc (dialect, private fields, framing, response codes); resolve §5 with compliance | 1–2 weeks, mostly waiting on the vendor/compliance, not engineering |
| 1. Core listener | TCP server, message framing, bitmap parse/serialize (evaluate `pyiso8583` or an equivalent codec vs. hand-rolled), unit tests against sample messages | 2–3 weeks |
| 2. Field mapping + decision integration | DE↔canonical mapping, call into existing `decide()`, response code mapping, PAN handling per §5 resolution | 1–2 weeks |
| 3. Deployment | New deployable, its own health checks/observability (mirrors `decision_service`'s existing pattern), fail-open/closed wiring, shadow-mode default | 1 week |
| 4. Testing | Unit + integration against recorded/sample authorization messages, then vendor certification testing (external, not fully Fraud360-controlled timeline) | 2–4 weeks incl. external cert |

**Total engineering effort: roughly 6–9 weeks**, not counting compliance sign-off time or
vendor certification scheduling, both of which are typically the longer pole.

## 9. Recommended Next Steps

1. Confirm with the target bank whether a **payment hub / switch abstraction layer** already
   sits in front of the raw switch and already normalizes 8583 to REST/XML for other
   downstream systems (many banks have one). If so, integrate there instead — materially
   cheaper than a native listener, and answers §7.1–7.4 for free.
2. If no such hub exists, route §5 (PCI scope) to compliance/security now, in parallel with
   naming a pilot vendor for §7.
3. Once both come back, resolve the private-field mapping and dialect against that vendor's
   actual spec, and this moves from Partial to Built.

## 10. What Exists Today (added once building started)

| Module | What it does | Tests |
|---|---|---|
| `app/spec.py` | Standard ISO 8583:1987 field definitions (DE2/3/4/7/11/32/37/39/41/42/49 only — deliberately not the private-use ranges, see §4/§7) | — |
| `app/codec.py` | Bitmap + LLVAR/LLLVAR/fixed-field encode/decode, 2-byte length-header TCP framing | `test_iso8583_codec.py` (14 tests) |
| `app/mapper.py` | DE ↔ canonical `DecideIn`/`DecideOut` mapping, PAN masking (SHA-256 + last 4, never raw), DE7 year-less date resolved against an explicit configured offset (`ISO8583_TZ_OFFSET_MINUTES`, default +330/IST), response-code mapping | `test_iso8583_mapper.py` (21 tests) |
| `app/client.py` | Calls `POST /decide` exactly as a JSON integrator would — machine-credential token exchange against `/machine/token`, cached, refreshed on 401 | exercised via server tests |
| `app/server.py` | asyncio TCP connection handler: every failure path (malformed message, unreachable `/decide`, unrecognised `/decide` response) returns an explicit refusal, never a guessed approval | `test_iso8583_server.py` (10 tests) |
| `app/main.py` | FastAPI entrypoint for `/health`; starts the TCP listener on startup; **refuses to start** if `ISO8583_FAIL_OPEN` is unset — no silent default, matching `RailPolicy.fail`'s own discipline | — |

New settings (`cp_common.settings`, all under the `ISO8583_*` env prefix):
`ISO8583_GATEWAY_HOST`/`PORT` (bind address), `ISO8583_CLIENT_ID`/`SECRET`/`TENANT_ID`
(the machine credential this gateway authenticates with — same mechanism as any Lane A
integrator, §3.2 of the integration spec), `ISO8583_TZ_OFFSET_MINUTES` (default 330/IST),
`ISO8583_FAIL_OPEN` (bool, **no default** — must be set explicitly).

**Not wired into `run_local.ps1` or `docker-compose.yml`.** Both require credentials that
don't exist for any tenant yet, and starting it in the default dev topology would just
crash-loop. Run it standalone once a tenant and credential exist:

```powershell
$env:ISO8583_CLIENT_ID="svc_..."; $env:ISO8583_CLIENT_SECRET="..."
$env:ISO8583_TENANT_ID="<tenant-uuid>"; $env:ISO8583_FAIL_OPEN="true"   # or "false" - bank's call, §6
.\.venv\Scripts\python -m uvicorn services.iso8583_gateway.app.main:app --host 0.0.0.0 --port 8000
# TCP listener itself binds ISO8583_GATEWAY_PORT (default 8588), separate from the health port above
```

No database migration was needed — this component has no models and no schema of its own;
it is a pure protocol adapter calling out to `/decide` over HTTP.

---
*Sections 1–9 above are the original proposal and remain the plan. §10 reflects what has
actually been built and tested since; the open questions in §7 are unchanged by it.*
