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

## Two verification modes

**Async (default)** — for low/medium-risk actions

```python
@expect(postcondition="row_exists", table="notes", match_field="note_id")
async def log_note(note_id, text):
    ...
```

Fire-and-forget. Your agent doesn't wait — verification happens in the background. Zero latency added to your agent's response path.

**Sync** — for high-impact actions (payments, bookings, anything user-facing)

```python
@expect(postcondition="row_exists", table="bookings", match_field="booking_id", sync=True)
async def confirm_booking(booking_id):
    ...
```

When `sync=True`, Synathic verifies before your function returns. If the write didn't actually land, your agent knows immediately — not three days later from a support ticket. Verified latency: **20-35ms** once the connection pool is warm (first call after startup may take longer while the pool initializes — this happens once, not per-request).

**Use sync when the user is waiting on the other end of the action. Use async for everything else.**

## When NOT to use Synathic

- If your writes are already verified synchronously by your own framework, you don't need this.
- Synathic verifies *write success* — that the row exists with the right value. It does not verify *business-outcome correctness* (e.g., that a booking is the *right* booking). That's domain logic, not ours.
- Single-table Postgres checks only, right now. If your critical writes span multiple tables or services, this covers part of the picture, not all of it.

## Guarantees

- **Deterministic:** SQL query, not an LLM guess. Either the row exists or it doesn't.
- **Non-blocking by default:** Async mode adds zero latency. Sync mode is opt-in, only where you need it.
- **Fire-and-forget on failure:** If Synathic's backend is down, your agent keeps running — verification failing to report never breaks your agent.
- **Safe against malformed input:** Table names are checked against an explicit whitelist before touching SQL. A malicious or malformed table value returns a clear 400 error — it never reaches the database.

## Supported Verifications

| Status | Postcondition | Notes |
|---|---|---|
| ✅ | `row_exists` | PostgreSQL |
| 🚧 | `row_not_exists` | |
| 🚧 | `field_equals` | |
| 🚧 | `endpoint_returns` | REST GET |

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


## Architecture notes (for the curious)

- Backend: FastAPI + PostgreSQL, connection-pooled (no per-request connection overhead)
- SDK: lightweight async HTTP client, reuses a persistent connection
- No Redis, no Celery, no microservices — a single deployable backend

## Status

Early. MIT-licensed, launched August 2026. `row_exists` on PostgreSQL is the only postcondition live today — everything marked 🚧 above is scoped but not shipped. If you try it and something's missing, that's expected; tell us what you actually needed and it'll shape what gets built next.

## License

MIT (open source)

## Questions / feedback

hellosynathic@gmail.com — direct line to the founder.
