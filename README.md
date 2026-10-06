# Synathic SDK

> Verify what your AI agent actually did — not what it said it did.

## The 200 OK that lied

    agent_report: create_customer() → "success"
    tool_response: 200 OK
    synathic_check: checking postgres…
    ✘ ROW NOT FOUND

    Agent claimed success. The row was never written.
    Caught by a deterministic database check — not 3 days from a support ticket.

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

Python `>=3.10` is required. The SDK installs `asyncpg` for client-side verification.

```python
from synathic import monitor, expect

monitor.start(db_dsn="postgresql://synathic_ro:***@your-db:5432/yourdb")

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    # your agent logic — unchanged
    ...
```

Choose exactly one verification path when starting the monitor: `db_dsn` for client-side verification, or `demo_mode=True` to send events to the local Synathic backend. Omitting both or passing both raises `ValueError`.

At decoration time, `inspect.signature` checks that `match_field` names a declared function parameter; a typo raises `TypeError` immediately. If that argument is `None` at call time, the decorator raises `ValueError`.

With `db_dsn`, the SDK checks the customer's Postgres directly; the database credentials never travel to Synathic. With `demo_mode=True`, the event goes to the backend at `localhost:8000`, which checks its own Postgres.

By default, postconditions filter against `updated_at > execution_start`. This timestamp is a freshness signal, not proof that this invocation caused the write. If a table does not track updates, pass `timestamp_column=None` to opt out. The SDK annotation is `Optional[str]`.

> **Important today:** `table` and `match_field` must come from a fixed, hardcoded whitelist — currently `customers`, `executions`, `events`, `verifications`, each with a fixed set of allowed columns (see `backend/app/routes.py::ALLOWED_TABLES`). There is no config yet to point Synathic at your own schema. This is the main thing blocking anyone outside this repo from using it as-is — see **Known Limitations**.

## Two verification modes

**Async (default)** — for low/medium-risk actions

```python
@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    ...
```

The SDK sends the event to the backend, which runs postcondition verification in a background task. The function does not wait for the verification result, but it does await event submission.

**Sync** — for high-impact actions (payments, bookings, anything user-facing)

```python
@expect(postcondition="row_exists", table="customers", match_field="email", sync=True)
async def create_customer(email, name):
    ...
```

When `sync=True`, Synathic verifies before your function returns. If the write didn't actually land, your agent knows immediately — not three days later from a support ticket.

**Use sync when the user is waiting on the other end of the action. Use async for everything else.**

## Supported Verifications

| Status | Postcondition | Notes |
|---|---|---|
| ✅ | `row_exists` | PostgreSQL. Checks for a matching row, with freshness filtering by default. |
| ✅ | `row_not_exists` | PostgreSQL. Checks for no matching fresh row by default. |
| ✅ | `field_equals` | PostgreSQL. Compares a column against an expected value, with freshness filtering by default. |
| 🚧 | `endpoint_returns` | REST GET — not started. |

All three implemented postconditions are handled in `backend/app/routes.py::_perform_verification` and covered by `backend/test_e2e.py` (9 cases: pass/fail for each postcondition type, plus 3 causality cases).

**Known gap:** `row_exists`, `row_not_exists`, and `field_equals` apply a freshness check using `timestamp_column > execution_start`. The default timestamp column is `updated_at`; the target table must have the selected timestamp column, which must also be allowed by the fixed whitelist, or freshness checking must be explicitly disabled with `timestamp_column=None`. A timestamp anchor is a freshness signal, not proof of causality.

## Known Limitations (current, as of this repo)

- **Fixed table whitelist.** Only 4 tables/columns are checkable, hardcoded in `routes.py`. No env-var, config file, or schema introspection yet. Anyone outside this project has to fork the backend to check their own tables.
- **Causality vs. existence gap.** All three supported postconditions filter against `execution_start` using `updated_at` by default. The remaining limitation is that the target table must contain the selected, whitelisted timestamp column; the timestamp is a freshness signal, not proof of causality.
- **Single-table, single-database checks only.** PostgreSQL only. A write that spans multiple tables or services is only partially covered by a single postcondition.
- **Client-side path requires network access.** The process running the SDK must be able to reach the customer's Postgres using the supplied DSN.
- **Async validation errors are not returned in the event response.** A verification exception is logged and recorded with status `unknown`; the original async event request has already returned.
- **Fixed demo backend whitelist.** Path A's backend checks only the tables and columns in `ALLOWED_TABLES`; Path B validates identifier syntax but does not use that backend whitelist.
- **API key configuration is optional.** When `SYNATHIC_API_KEY` is set, API requests require `Authorization: Bearer <key>` and the SDK sends the configured key. When unset, the backend accepts requests without authentication. Set it before exposing the backend beyond a trusted environment. CORS origins are configurable with `SYNATHIC_CORS_ORIGINS`; an unset or empty value falls back to `*`.

## Roadmap

**Done**
- Causality/existence fix: freshness is anchored to the execution start timestamp for the three supported postconditions.
- API-key authentication using `SYNATHIC_API_KEY` and Bearer headers.
- Failure classifier wired into verification records and `GET /api/executions/{id}`.
- Client-side verification path using `db_dsn`.

**Planned, not started**
- Configurable table/column checks, replacing the hardcoded whitelist — prerequisite before this is usable outside this repo.
- `endpoint_returns` postcondition (REST GET).
- MySQL support.

## Supported Frameworks

| Status | Framework |
|---|---|
| ✅ | Any Python async function (manual `@expect` decorator) |
| 🚧 | LangGraph native callback handler |
| 🚧 | CrewAI |

## How it works

    [Your Agent]
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
- **Async verification:** the backend runs the postcondition check in a background task; the SDK still awaits event submission, but not the verification result.
- **Backend request failures:** the SDK suppresses exceptions from `send_event`; an HTTP timeout can still delay event submission.
- **Failure classification:** failed verifications include a deterministic category, exposed as `failure_category` by `GET /api/executions/{id}`.
- **SQL identifier validation:** `/events-verify` returns a clear `400` for table/column names outside the whitelist. In the async `/events` path, verification exceptions are logged and recorded as `unknown`; they are not returned in the original event response — see Known Limitations.

## Architecture notes (for the curious)

- Backend: FastAPI + PostgreSQL, connection-pooled (no per-request connection overhead)
- SDK: lightweight async HTTP client, reuses a persistent connection
- No Redis, no Celery, no microservices — a single deployable backend

## Status

Early-stage software. MIT-licensed. `row_exists`, `row_not_exists`, `field_equals`, client-side verification, API-key authentication, and failure classification are implemented; the end-to-end suite covers 9 cases. Synathic is not presented as production-proven, and the demo backend still uses a fixed table/column whitelist.

## License

MIT (open source)

## Questions / feedback

hellosynathic@gmail.com — direct line to the founder.
