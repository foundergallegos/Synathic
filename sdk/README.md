# Synathic SDK

> Verify what your AI agent actually did — not what it said it did.

## What is Synathic?

Synathic is a verification layer for AI agents. After your agent runs, we check your PostgreSQL database to confirm the side effects actually happened.

**Not observability. Not tracing. Verification.**

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

Synathic checks Postgres after your function runs and tells you PASS or FAIL.

## Guarantees

1. **Non-blocking:** Async mode adds zero latency. Sync mode is opt-in.
2. **Fire-and-forget:** If Synathic's backend is down, your agent keeps running.
3. **Deterministic:** SQL query, not an LLM guess.

## License

MIT (open source)