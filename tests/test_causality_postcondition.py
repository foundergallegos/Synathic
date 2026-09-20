import uuid
from datetime import datetime, timedelta

import asyncpg
import pytest

from backend.app.database import async_session
from backend.app.routes import events_verify

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"


@pytest.mark.asyncio
async def test_row_exists_requires_execution_caused_current_state_in_sync_verification():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                name TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
        await conn.execute(
            "ALTER TABLE IF EXISTS customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()"
        )

        email = f"causal-{uuid.uuid4()}@example.com"
        stale_time = datetime.utcnow() - timedelta(hours=1)
        await conn.execute(
            "INSERT INTO customers (name, email, updated_at) VALUES ($1, $2, $3)",
            "stale-user",
            email,
            stale_time,
        )

        stale_execution_start = datetime.utcnow().isoformat()
        async with async_session() as db:
            resp = await events_verify(
                {
                    "execution_id": str(uuid.uuid4()),
                    "event_type": "tool_result",
                    "payload": {
                        "_skip_bg_verify": True,
                        "postcondition": {
                            "type": "row_exists",
                            "table": "customers",
                            "field": "email",
                            "value": email,
                            "timestamp_column": "updated_at",
                            "execution_start": stale_execution_start,
                        },
                    },
                },
                db=db,
            )
            assert resp["status"] == "fail"

        fresh_execution_start = datetime.utcnow().isoformat()
        await conn.execute(
            "UPDATE customers SET updated_at = $1 WHERE email = $2",
            datetime.utcnow(),
            email,
        )

        async with async_session() as db:
            resp = await events_verify(
                {
                    "execution_id": str(uuid.uuid4()),
                    "event_type": "tool_result",
                    "payload": {
                        "_skip_bg_verify": True,
                        "postcondition": {
                            "type": "row_exists",
                            "table": "customers",
                            "field": "email",
                            "value": email,
                            "timestamp_column": "updated_at",
                            "execution_start": fresh_execution_start,
                        },
                    },
                },
                db=db,
            )
            assert resp["status"] == "pass"
    finally:
        await conn.execute("DELETE FROM customers WHERE email = $1", email)
        await conn.close()
