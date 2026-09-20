import asyncio
import uuid
from datetime import datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.app.routes import _perform_verification, verify_postcondition

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/synathic"


def test_value_none_gives_clear_error():
    execution_id = str(uuid.uuid4())
    postcondition = {
        "type": "row_exists",
        "table": "customers",
        "field": "email",
        "value": None,
        "timestamp_column": "updated_at",
        "execution_start": datetime.utcnow().isoformat(),
    }

    async def _run():
        engine = create_async_engine(DATABASE_URL, echo=False)
        SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with SessionLocal() as db:
                with pytest.raises(ValueError, match="postcondition value cannot be None"):
                    await _perform_verification(db, execution_id, postcondition)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_field_equals_rejects_old_row():
    execution_id = str(uuid.uuid4())
    email = f"old-row-{uuid.uuid4()}@example.com"
    stale_time = datetime.utcnow() - timedelta(hours=1)

    async def _run():
        engine = create_async_engine(DATABASE_URL, echo=False)
        SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with SessionLocal() as db:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS customers (
                        id SERIAL PRIMARY KEY,
                        name TEXT,
                        email TEXT,
                        created_at TIMESTAMP DEFAULT now(),
                        updated_at TIMESTAMP DEFAULT now()
                    )
                """))
                await db.execute(
                    text("INSERT INTO customers (name, email, updated_at) VALUES (:name, :email, :updated_at)"),
                    {"name": "old-user", "email": email, "updated_at": stale_time},
                )
                await db.commit()

                postcondition = {
                    "type": "field_equals",
                    "table": "customers",
                    "field": "email",
                    "value": email,
                    "expected_field": "name",
                    "expected_value": "old-user",
                    "timestamp_column": "updated_at",
                    "execution_start": datetime.utcnow().isoformat(),
                }

                status, _ = await _perform_verification(db, execution_id, postcondition)
                assert status == "fail"

                await db.execute(text("DELETE FROM customers WHERE email = :email"), {"email": email})
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_verify_postcondition_records_unknown_on_exception():
    execution_id = str(uuid.uuid4())
    postcondition = {
        "type": "row_exists",
        "table": "table_does_not_exist",
        "field": "email",
        "value": "someone@example.com",
        "timestamp_column": "updated_at",
        "execution_start": datetime.utcnow().isoformat(),
    }

    async def _run():
        await verify_postcondition(execution_id, postcondition)

        conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/synathic")
        try:
            row = await conn.fetchrow(
                "SELECT status, error_message FROM verifications WHERE execution_id = $1 ORDER BY checked_at DESC LIMIT 1",
                execution_id,
            )
            assert row is not None
            assert row["status"] == "unknown"
            assert "table_does_not_exist" in (row["error_message"] or "")
        finally:
            await conn.close()

    asyncio.run(_run())
