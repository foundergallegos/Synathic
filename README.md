# Synathic SDK

> Verify what your AI agent actually did — not what it said it did.

## The 200 OK that lied

    agent_report: create_customer() → "success"
    tool_response: 200 OK
    synathic_check: checking postgres…
    ✘ ROW NOT FOUND

    Agent claimed success. The row was never written.
    Caught in 400ms — not 3 days from a support ticket.

That's what Synathic catches. A "200 OK" from your agent's tool call doesn't mean the database row exists. Synathic checks reality, deterministically — no LLM judging its own work, just SQL.

## What is Synathic?

Synathic is a verification layer for AI agents. After your agent runs, we check your PostgreSQL database to confirm the side effects actually happened.

**Not observability. Not tracing. Verification.**

## Why not just try/except?

Error handling catches *explicit* failures — exceptions, non-200s, timeouts. It doesn't catch the failure mode where nothing throws: the write is silently rejected by a downstream validation rule, an async callback fires out of order, or a replica hasn't caught up yet. The logs stay green. Nobody notices until someone reconciles the data days later.

Synathic doesn't replace your error handling — it checks the one thing error handling can't see: whether the state you expected actually landed.

## Quick Start

```bash
pip install synathic
```

```python
from synathic import monitor, expect

monitor.start()

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    # your agent logic — unchanged
    ...
```

That's it. Synathic checks Postgres after your function runs and tells you PASS or FAIL.
By default, `row_exists` verifies both that the row exists and that its current state was written by this execution via the `updated_at` freshness check. If a table does not track updates, pass `timestamp_column=None` to explicitly opt out of freshness checking.

> **Important today:** `table` and `match_field` must come from a fixed, hardcoded whitelist — currently `customers`, `executions`, `events`, `verifications`, each with a fixed set of allowed columns (see `backend/app/routes.py::ALLOWED_TABLES`). There is no config yet to point Synathic at your own schema. This is the main thing blocking anyone outside this repo from using it as-is — see **Known Limitations**.

## Two verification modes

**Async (default)** — for low/medium-risk actions

```python
@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    ...
```

Fire-and-forget. Your agent doesn't wait — verification happens in the background. Zero latency added to your agent's response path.

**Sync** — for high-impact actions (payments, bookings, anything user-facing)

```python
@expect(postcondition="row_exists", table="customers", match_field="email", sync=True)
async def create_customer(email, name):
    ...
```

When `sync=True`, Synathic verifies before your function returns. If the write didn't actually land, your agent knows immediately — not three days later from a support ticket.

*Note: the README previously quoted "20-35ms once the pool is warm" for sync latency. We don't have a benchmark or test in this repo backing that number — treat it as unverified until it's actually measured, per our own no-fabricated-metrics rule.*

<<<<<<< HEAD
**Use sync when the user is waiting on the other end of the action. Use async for everything else.**

## Supported Verifications

| Status | Postcondition | Notes |
|---|---|---|
| ✅ | `row_exists` | PostgreSQL. Confirms a matching row exists at check time. |
| ✅ | `row_not_exists` | PostgreSQL. |
| ✅ | `field_equals` | PostgreSQL. Compares a specific column's actual value against an expected one. |
| 🚧 | `endpoint_returns` | REST GET — not started. |

All three ✅ postconditions are implemented in `backend/app/routes.py::_perform_verification` and covered end-to-end in `backend/test_e2e.py` (6 passing cases: pass/fail for each type) against a live Postgres instance.

**Known gap:** `row_exists` currently confirms *a* row exists — not that *this invocation* wrote it. A row inserted before your agent ever ran, with the same match value, will still show `PASS`. That's a real false-positive risk. Fix is scoped (timestamp-anchor check) but not shipped — see Roadmap.

## Known Limitations (current, as of this repo)

- **Fixed table whitelist.** Only 4 tables/columns are checkable, hardcoded in `routes.py`. No env-var, config file, or schema introspection yet. Anyone outside this project has to fork the backend to check their own tables.
- **No enforced authentication.** `monitor.start(api_key=...)` accepts a key, but it is never sent as a header or validated anywhere in the request path. Any client that can reach the API can post events and read verification results. CORS is currently wide open (`allow_origins=["*"]`). Do not point this at a public endpoint.
- **Causality vs. existence gap.** See above — `row_exists` can false-positive on stale/pre-existing rows. Documented and approved for the roadmap, not yet implemented.
- **Failure classifier not wired into the API.** `backend/app/classifier.py` implements deterministic failure categorization (race condition, stale row, value mismatch, format mismatch) and has its own internal smoke test, but no API endpoint calls it yet. It's not reachable from `/api/events` or `/api/events-verify` today — implemented, not integrated.
- **Single-table, single-database checks only.** PostgreSQL only. A write that spans multiple tables or services is only partially covered by a single postcondition.
- **Async-path error handling is silent.** On the default async flow, an invalid/malformed table name is caught in the background task and logged server-side, but no error is surfaced to the caller. The sync path (`/events-verify`) does return a proper `400` for the same case — the two paths behave differently today.

## Roadmap

**In progress**
- Causality/existence fix: timestamp-anchor check (verify the row's `updated_at`/insert time is after the agent's start time, not just that a row exists).

**Planned, not started**
- Configurable table/column checks, replacing the hardcoded whitelist — prerequisite before this is usable outside this repo.
- Real API authentication.
- Wiring the failure classifier into the verification response so callers get a category, not just pass/fail.
- `endpoint_returns` postcondition (REST GET).
- Longer-term direction: MySQL support, broader REST API checks, governance/audit tooling.

## Supported Frameworks

| Status | Framework |
|---|---|
| ✅ | Any Python async function (manual `@expect` decorator) |
| 🚧 | LangGraph native callback handler |
| 🚧 | CrewAI |

## How it works

    [Your Agent]
=======
Guarantees
Deterministic: SQL query, not an LLM guess. `row_exists` requires both presence and freshness: the matching row must have been updated after the current execution started, so stale rows cannot falsely pass.
Non-blocking by default: Async mode adds zero latency. Sync mode is opt-in, only where you need it.
Fire-and-forget on failure: If Synathic's backend is down, your agent keeps running — verification failing to report never breaks your agent.
Safe against malformed input: Table names are checked against an explicit whitelist before touching SQL. A malicious or malformed table value returns a clear 400 error — it never reaches the database.
If a target table has no timestamp column, use `timestamp_column=None` explicitly. The default is `updated_at`, and a missing column raises a clear verification error instead of silently weakening the guarantee.
Supported Verifications
✅ row_exists — PostgreSQL
🚧 row_not_exists
🚧 field_equals
🚧 endpoint_returns — REST GET
Supported Frameworks
✅ Any Python async function (manual @expect decorator)
🚧 LangGraph native callback handler (coming soon)
🚧 CrewAI (coming soon)
How it works
[Your Agent] 
>>>>>>> f64f99b (Fix causality check on field_equals, explicit error on value=None, unknown status on verification exceptions, clean pycache tracking)
    → wraps function with @expect(...)
    → runs normally, logic unchanged
    → SDK sends event to Synathic backend (async or sync, your choice)

    [Synathic Backend]
    → records the execution + event
    → checks PostgreSQL: does the row actually exist?
    → returns PASS or FAIL

    [You]
    → see reality, not the agent's claim

## Guarantees

- **Deterministic:** SQL query, not an LLM guess. Either the row exists or it doesn't.
- **Non-blocking by default:** async mode adds zero latency. Sync mode is opt-in, only where you need it.
- **Fire-and-forget on backend-down:** if Synathic's backend is unreachable, your agent keeps running — verification failing to report never breaks your agent.
- **SQL-injection safe on the sync path:** `/events-verify` checks table/column names against an explicit whitelist before touching SQL and returns a clear `400` on a bad name. The default async path (`/events`) currently swallows the same error silently instead of surfacing it — see Known Limitations.

## Architecture notes (for the curious)

- Backend: FastAPI + PostgreSQL, connection-pooled (no per-request connection overhead)
- SDK: lightweight async HTTP client, reuses a persistent connection
- No Redis, no Celery, no microservices — a single deployable backend

## Status

Early. MIT-licensed, launched August 2026. `row_exists`, `row_not_exists`, and `field_equals` are implemented and covered by an automated end-to-end test suite against a live Postgres instance. Not yet usable against a schema outside the built-in table whitelist, and not yet authenticated — treat this as a local/trusted-environment tool until those two items ship.

## License

MIT (open source)

## Questions / feedback

hellosynathic@gmail.com — direct line to the founder.
