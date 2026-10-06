# Synathic

> Verify what your AI agent actually did — not what it said it did.

## The 200 OK that lied

    agent_report: create_customer() → "success"
    tool_response: 200 OK
    synathic_check: checking postgres…
    ✘ ROW NOT FOUND

    Agent claimed success. The row was never written.
    Caught right after the call — not 3 days later from a support ticket.

A "200 OK" from your agent's tool call doesn't mean the database row exists. Synathic checks reality, deterministically: no LLM judging its own work, just a SQL query.

## What is Synathic?

Synathic is a Python decorator (`@expect`) that, after an agent function runs, verifies with a direct SQL query against Postgres whether the expected side effect really happened.

**Not observability. Not tracing. Verification.**

## Why not just try/except?

Error handling catches *explicit* failures: exceptions, non-200s, timeouts. It doesn't catch the failure mode where nothing throws: the write is silently rejected by a downstream validation rule, an async callback fires out of order, or a replica hasn't caught up yet. The logs stay green. Nobody notices until someone reconciles the data days later.

Synathic doesn't replace your error handling. It checks the one thing error handling can't see: whether the state you expected actually landed.

## Quick Start

```bash
pip install synathic
```

```python
from synathic import monitor, expect

# Verify against YOUR Postgres, using a read-only role (recommended)
monitor.start(db_dsn="postgresql://synathic_ro:***@your-db:5432/yourdb")

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    # your agent logic — unchanged
    ...
```

Synathic runs a `SELECT` after your function returns and tells you `pass` or `fail`.

`monitor.start()` requires you to choose a verification path explicitly. With neither `db_dsn` nor `demo_mode=True`, it won't start. There is no silent default.

### Minimal read-only role

Synathic only ever needs `SELECT`. Give it a role that can do nothing else:

```sql
CREATE ROLE synathic_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE yourdb TO synathic_ro;
GRANT USAGE ON SCHEMA public TO synathic_ro;
GRANT SELECT ON customers TO synathic_ro;
```

## Two verification paths

### Path B — Client-side (production): `db_dsn=...`

The SDK opens its own connection (`asyncpg`) directly to your Postgres using the DSN you provide. The `SELECT` runs *inside your infrastructure*, with your credentials. The pass/fail result can optionally be forwarded to the Synathic backend for history.

**Your database credentials never travel to Synathic.**

```python
monitor.start(db_dsn="postgresql://synathic_ro:***@your-db:5432/yourdb")
```

### Path A — Demo (against Synathic's own infrastructure): `demo_mode=True`

The SDK sends the event over HTTP to the backend (FastAPI), which runs the `SELECT` against its own Postgres. Useful for trying the mechanism without pointing it at a real database. Every verification in this mode is explicitly marked as demo, so it can never be confused with a real one.

```python
monitor.start(demo_mode=True)
```

## Two execution modes

**Async (default)** — for low/medium-risk actions. Doesn't block your agent; verification happens in the background.

```python
@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    ...
```

**Sync** — for high-impact actions (payments, bookings, anything user-facing). The result is returned together with your function's response, blocking until verification completes. If the write didn't land, your agent knows immediately.

```python
@expect(postcondition="row_exists", table="customers", match_field="email", sync=True)
async def create_customer(email, name):
    ...
```

**Use sync when the user is waiting on the other end of the action. Use async for everything else.**

## Supported verifications

| Status | Postcondition | Passes when… |
|---|---|---|
| ✅ | `row_exists` | A row exists where `match_field = value`. |
| ✅ | `row_not_exists` | No such row exists. |
| ✅ | `field_equals` | The row exists **and** `expected_field` equals `expected_value`. |
| 🚧 | `endpoint_returns` | REST GET — not started. |

```python
@expect(
    postcondition="field_equals",
    table="customers",
    match_field="email",
    expected_field="name",
    expected_value="Alice",
)
async def rename_customer(email, name):
    ...
```

### Causality, not just existence

By default, `row_exists` confirms that *a* row exists, not that *this invocation* wrote it. A stale row with the same match value would produce a false `pass`.

To close that gap, all three postconditions accept an optional `timestamp_column`:

```python
@expect(
    postcondition="row_exists",
    table="customers",
    match_field="email",
    timestamp_column="created_at",
)
async def create_customer(email, name):
    ...
```

When set, the check requires that column to have advanced since a timestamp captured **before** the agent ran (`SELECT now()` on the same connection, to avoid clock skew between machines). An old row with the same value no longer produces a false `pass`.

There is deliberately no forced default: you specify the column explicitly, per table.

## Failure classification

When a verification ends in `fail`, Synathic classifies the probable cause with simple, deterministic rules (no AI model) and stores the category alongside the record. It is exposed in `GET /api/executions/{id}`.

| Category | Meaning |
|---|---|
| `row_never_inserted` | The row never appeared, with no evidence of a race. |
| `race_condition` | The row appeared after the check had already looked. |
| `stale_row` | For `row_not_exists`: the row already existed beforehand. |
| `value_mismatch` | The row exists but the field value differs (typo, case, whitespace…). |
| `format_mismatch` | The row exists but the value differs in type or format (`1` vs `"1"`, timestamps…). |
| `unclassified` | No rule applies, or there isn't enough metadata to decide. |

Each classification includes the rules that fired and the evidence compared, so it's explainable.

## How it works

1. You decorate an async function with `@expect(...)`.
2. **At decoration time** (not on every call), Synathic validates that `match_field` is actually a parameter of the function. A typo blows up there, not in production.
3. **On each call**, the real value of `match_field` is resolved by name (`inspect.signature(...).bind(...)`), never by position.
4. If that value is `None`, nothing is verified and an explicit error is returned, instead of letting `field = NULL` produce a misleading pass/fail.
5. Your agent function runs normally, unchanged.
6. Depending on the path (A or B), the corresponding `SELECT` runs and yields `pass` / `fail`.
7. With `sync=True` the result is returned with your function's response. In async mode nothing is blocked.

```
[Your Agent]
  → function wrapped with @expect(...)
  → runs normally, logic unchanged

[Synathic SDK]
  → Path B: SELECT against your Postgres, with your read-only DSN
  → Path A (demo): event sent to Synathic backend, which runs the SELECT
  → result: pass / fail (+ failure category)

[You]
  → see reality, not the agent's claim
```

## Security model

- **SQL identifiers.** Table and column names can't be parameterized, so they are validated against a strict pattern (`^[a-z_][a-zA-Z0-9_]*$`) and quoted before interpolation. Path A additionally uses a static whitelist of its own tables/columns. Values are always passed as bound parameters.
- **Data redaction.** By default (`capture_args=False`), the SDK does not send your function's arguments or full results to the backend, only what verification needs. Sending the full payload is an explicit opt-in (`capture_args=True`).
- **Error sanitization.** A connection failure to your Postgres (wrong password, missing table permission…) is normalized to a generic message before being logged or forwarded. The full DSN, which carries the password, is never exposed.
- **API authentication.** Every request under `/api` is validated against an API key configured through an environment variable and sent as `Authorization: Bearer`. CORS is restricted to explicitly configured origins, not left open.

## Guarantees

- **Deterministic:** a SQL query, not an LLM guess. Either the row exists or it doesn't.
- **Non-blocking by default:** async mode doesn't put verification on your agent's response path. Sync mode is opt-in, only where you need it.
- **Fire-and-forget on backend-down:** if the Synathic backend is unreachable, your agent keeps running. Failing to report never breaks your agent.
- **Your credentials stay with you** on the client-side path.

## Limitations

- **PostgreSQL only**, one table per verification. A write spanning multiple tables or services is only partially covered by a single postcondition.
- **Verifies that the write arrived, not that the business outcome is correct.**
- **No duplicate detection** (`exactly_one`) or concurrent-write detection. Out of scope for now.
- **The client-side path needs network reach** from the agent process to your database.

## Roadmap

- `endpoint_returns` postcondition (REST GET).
- Longer-term: MySQL support, broader REST API checks, governance/audit tooling.

## Supported frameworks

| Status | Framework |
|---|---|
| ✅ | Any Python async function (manual `@expect` decorator) |
| 🚧 | LangGraph native callback handler |
| 🚧 | CrewAI |

## Testing

```bash
pytest
```

The suite covers `row_exists` / `row_not_exists` / `field_equals` end-to-end against a real Postgres (6 cases, pass and fail for each type), SQL-injection attempts on table names, `match_field` resolution and `value=None` handling, causality with a stale row, and `401` on missing credentials. The client-side path is covered by a smoke test using a `SELECT`-only Postgres role, including the missing-permission case.

## Status

Early. MIT-licensed, launched August 2026. This README describes the architecture and test coverage. It is not evidence of production use by a customer or of measured usage metrics.

## License

MIT

## Questions / feedback

hellosynathic@gmail.com — direct line to the founder.