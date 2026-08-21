Synathic SDK

Verify what your AI agent actually did — not what it said it did.

What is Synathic?

Synathic is a verification layer for AI agents. After your agent runs, we check your PostgreSQL database to confirm the side effects actually happened.

Not observability. Not tracing. Verification.

A "200 OK" from your agent's tool call doesn't mean the database row exists. Synathic checks reality, deterministically — no LLM judging its own work, just SQL.

Quick Start
bash
pip install synathic
python
from synathic import monitor, expect

monitor.start()

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email, name):
    # your agent logic — unchanged
    ...

That's it. Synathic checks Postgres after your function runs and tells you PASS or FAIL.

Two verification modes
Async (default) — for low/medium-risk actions
python
@expect(postcondition="row_exists", table="notes", match_field="note_id")
async def log_note(note_id, text):
    ...

Fire-and-forget. Your agent doesn't wait — verification happens in the background. Zero latency added to your agent's response path.

Sync — for high-impact actions (payments, bookings, anything user-facing)
python
@expect(postcondition="row_exists", table="bookings", match_field="booking_id", sync=True)
async def confirm_booking(booking_id):
    ...

When sync=True, Synathic verifies before your function returns. If the write didn't actually land, your agent knows immediately — not three days later from a support ticket. Verified latency: 20-35ms once the connection pool is warm (first call after startup may take longer while the pool initializes — this happens once, not per-request).

Use sync mode when the user is waiting on the other end of the action. Use async for everything else.

Guarantees
Deterministic: SQL query, not an LLM guess. Either the row exists or it doesn't.
Non-blocking by default: Async mode adds zero latency. Sync mode is opt-in, only where you need it.
Fire-and-forget on failure: If Synathic's backend is down, your agent keeps running — verification failing to report never breaks your agent.
Safe against malformed input: Table names are checked against an explicit whitelist before touching SQL. A malicious or malformed table value returns a clear 400 error — it never reaches the database.
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
    → wraps function with @expect(...)
    → runs normally, logic unchanged
    → SDK sends event to Synathic backend (async or sync, your choice)

[Synathic Backend]
    → records the execution + event
    → checks PostgreSQL: does the row actually exist?
    → returns PASS or FAIL

[You]
    → see reality, not the agent's claim
Architecture notes (for the curious)
Backend: FastAPI + PostgreSQL, connection-pooled (no per-request connection overhead)
SDK: lightweight async HTTP client, reuses a persistent connection
No Redis, no Celery, no microservices — a single deployable backend
License

MIT (open source)

Questions / feedback

hellosynathic@gmail.com — direct line to the founder.
