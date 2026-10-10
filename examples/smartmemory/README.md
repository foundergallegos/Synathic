# Synathic to SmartMemory failure journal

A write that Synathic verifies and finds missing becomes a SmartMemory failure-journal memory, and a "check before retry" for the same action returns it.
The test uses real Postgres in Docker, real Synathic client-side verification and a real local SmartMemory (SQLite, no service).

Synathic does not raise on a failed postcondition. With `sync=True` and `monitor.start(db_dsn=...)` the decorated coroutine returns `{"verification": {"mode": "client", "status": "fail"}}`.
Verification errors (bad DSN, unreachable Postgres) do raise, and `journal_verification_errors` journals them as `synathic.verification.error` and re-raises.
The decorated function must be `async def`, because a plain function bypasses verification.

This is an opt-in example. It needs Docker, and the default `pytest` run of this repository skips it (see `conftest.py`). Set `SYNATHIC_SMARTMEMORY_EXAMPLE=1` to run it.

## Setup

From this folder:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The MiniLM embedding weights (about 100 MB) are fetched by the fixture, which calls `create_lite_memory(auto_download_models=True)`.
Setting only `SMARTMEMORY_HF_ALLOW_DOWNLOAD=1` is not enough on a machine with an empty Hugging Face cache, because the startup check passes `allow_download=False`.
To pre-provision by hand, run the test once with network access.

## Run

```
SYNATHIC_SMARTMEMORY_EXAMPLE=1 .venv/bin/pytest -q -p no:cacheprovider --basetemp=$PWD/pt-tmp test_integration.py
rm -rf pt-tmp
docker ps -a | grep synathic_test   # expect no output
```

Needs Docker with the `postgres:16` image. The fixture removes its container and its SmartMemory data directory, and fails the test if it cannot.

## Mapping

| Synathic | SmartMemory |
|---|---|
| `verification.status` (`fail`, or `unknown` on the background path) | `error_type` = `synathic.verification.<status>` |
| an exception from the decorated call | `error_type` = `synathic.verification.error` |
| `@expect` kwargs, agent result body | `content` (what was checked and what the agent returned) |
| action name and call arguments | `context` (what retry checks search on) |
| a `pass` result | nothing is recorded |

Written against synathic 0.1.2 and smartmemory-core 1.5.26.
