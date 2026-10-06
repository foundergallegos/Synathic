import asyncio
import uuid

import asyncpg
import httpx
import pytest

API_URL = "http://127.0.0.1:8000/api/events-verify"
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"


def post_event(payload):
    try:
        with httpx.Client(timeout=10.0) as client:
            return client.post(API_URL, json=payload)
    except httpx.ConnectError as exc:
        pytest.fail(f"Backend no disponible en {API_URL}: {exc}")


async def customers_table_count():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'customers'"
        )
    finally:
        await conn.close()


def test_sql_injection_table_rejected():
    payload = {
        "execution_id": str(uuid.uuid4()),
        "agent_name": "sql_injection_regression",
        "event_type": "tool_result",
        "payload": {
            "_skip_bg_verify": True,
            "postcondition": {
                "type": "row_exists",
                "table": "customers; DROP TABLE customers; --",
                "field": "email",
                "value": "attacker@example.com",
            },
        },
    }

    response = post_event(payload)
    assert response.status_code == 400
    body = response.json()
    assert body.get("error") in {"table_not_allowed", "invalid_postcondition"}

    table_count = asyncio.run(customers_table_count())
    assert table_count == 1


def test_sql_injection_column_rejected():
    payload = {
        "execution_id": str(uuid.uuid4()),
        "agent_name": "sql_injection_regression",
        "event_type": "tool_result",
        "payload": {
            "_skip_bg_verify": True,
            "postcondition": {
                "type": "row_exists",
                "table": "customers",
                "field": "email; DROP TABLE customers; --",
                "value": "attacker@example.com",
            },
        },
    }

    response = post_event(payload)
    assert response.status_code == 400
    body = response.json()
    assert body.get("error") in {"column_not_allowed", "invalid_postcondition"}
