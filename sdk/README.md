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

Requires Python `>=3.10`. The SDK package installs `asyncpg>=0.27.0` for direct client-side PostgreSQL verification.

```python
from synathic import monitor, expect

# Verify against YOUR Postgres, using a read-only role (recommended)
monitor.start(db_dsn="postgresql://synathic_ro:***@your-db:5432/yourdb")

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    # your agent logic — unchanged
    ...
```

`monitor.start()` requires exactly one verification path: `db_dsn` or `demo_mode=True`. With `db_dsn`, the SDK connects directly to your Postgres and runs the `SELECT` inside your infrastructure. Your database credentials are not sent to Synathic. With `demo_mode=True`, events go to the Synathic backend at `localhost:8000`, which checks its own Postgres.

### Minimal read-only role

Synathic only ever needs `SELECT`. Give it a role that can do nothing else:

```sql
CREATE ROLE synathic_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE yourdb TO synathic_ro;
GRANT USAGE ON SCHEMA public TO synathic_ro;
GRANT SELECT ON customers TO synathic_ro;
```

## Two verification paths

### Path B — Client-side: `db_dsn=...`

The SDK opens its own connection (`asyncpg`) directly to your Postgres using the DSN you provide. The `SELECT` runs *inside your infrastructure*. The database credentials stay in the client process and are not sent to Synathic; only the event and verification result may be forwarded to the backend.

**Your database credentials never travel to Synathic.**

```python
monitor.start(db_dsn="postgresql://synathic_ro:***@your-db:5432/yourdb")
```

### Path A — Demo (against Synathic's own infrastructure): `demo_mode=True`

The SDK sends the event over HTTP to the backend (FastAPI), which runs the `SELECT` against its own Postgres. This path is useful for trying the mechanism without pointing it at a real database. The backend checks its own database; it does not connect to the customer's database in this mode.

```python
monitor.start(demo_mode=True)
```

## Two execution modes

**Async (default)** — for low/medium-risk actions. The SDK submits the event and schedules verification in the background; it does not wait for the verification result, although event submission itself is awaited.

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

### Freshness, not proof of causality

The timestamp check reduces false passes caused by stale rows with the same match value, but does not prove that *this invocation* caused the write. By default, `timestamp_column` is `"updated_at"` and the check filters with `timestamp_column > execution_start`. The SDK captures `execution_start` as naive UTC using `datetime.now(timezone.utc)`. Use `timestamp_column=None` to opt out if the target table has no suitable timestamp column. The type annotation for the option is `Optional[str]`.

All three postconditions accept an optional `timestamp_column`:

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

When enabled, a row with the same match value but an older timestamp does not satisfy the freshness filter. The timestamp remains a temporal signal rather than definitive proof of causality.

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
4. If that value is `None`, the decorator raises an explicit `ValueError` instead of letting `field = NULL` produce a misleading pass/fail.
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

- **SQL identifiers.** Table and column names can't be parameterized, so client-side identifiers are checked against `^[a-z_][a-zA-Z0-9_]*$` before interpolation. Path A also uses a static whitelist of its tables and columns. Values are passed as bound parameters.
- **Connection error sanitization.** Client-side connection failures are logged and raised with a generic message; the DSN is not included in that message.
- **API authentication.** Requests under `/api` are validated against `SYNATHIC_API_KEY`; the SDK sends it as `Authorization: Bearer <key>` when configured. If the backend variable is unset, API requests are accepted without a key. CORS is configurable with `SYNATHIC_CORS_ORIGINS` and falls back to `*` when unset or empty.

## Guarantees

- **Deterministic:** a SQL query, not an LLM guess. Either the row exists or it doesn't.
- **Async verification:** the async mode does not wait for the check result, but awaits event submission; SDK request exceptions are suppressed by the event sender.
- **Your credentials stay with you** on the client-side path.

## Limitations

- **PostgreSQL only**, one table per verification. A write spanning multiple tables or services is only partially covered by a single postcondition.
- **Verifies that the write arrived, not that the business outcome is correct.**
- **No duplicate detection** (`exactly_one`) or concurrent-write detection. Out of scope for now.
- **The client-side path needs network reach** from the agent process to your database.

## Roadmap

- Configurable table/column checks to replace Path A's fixed whitelist.
- `endpoint_returns` postcondition (REST GET).
- MySQL support.

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

The E2E script covers 9 cases: pass/fail for `row_exists`, `row_not_exists`, and `field_equals`, plus 3 causality cases. Other tests cover SQL-injection rejection, `match_field` resolution, `value=None`, authentication, and client-side verification behavior.

## Status

Early-stage software. MIT-licensed. Client-side verification, demo mode, API-key authentication, and failure classification are implemented. The test suite documents current behavior; this is not evidence of production use by a customer or of measured usage metrics. Path A still uses the backend's fixed table/column whitelist.

## License

MIT

## Questions / feedback

hellosynathic@gmail.com — direct line to the founder.