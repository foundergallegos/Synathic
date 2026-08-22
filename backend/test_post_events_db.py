import asyncio
import uuid

import asyncpg
import httpx

# Debe coincidir con la DATABASE_URL de database.py (sin el prefijo +asyncpg).
# Password por defecto: postgres
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"
API_URL = "http://localhost:8000/api/events"


async def query_row(conn: asyncpg.Connection, table: str, execution_id: str):
    """Consulta directa a PostgreSQL. Solo acepta tablas hardcodeadas."""
    allowed = {"executions", "events", "customers"}
    if table not in allowed:
        raise ValueError(f"Tabla no permitida en test: {table}")

    return await conn.fetchrow(
        f"SELECT * FROM {table} WHERE execution_id = $1", execution_id
    )


async def main():
    execution_id = str(uuid.uuid4())
    payload_body = {
        "execution_id": execution_id,
        "agent_name": "test_agent",
        "event_type": "tool_result",
        "payload": {
            "result": "success",
            "postcondition": {
                "type": "row_exists",
                "table": "customers",
                "field": "email",
                "value": "db_test@example.com",
            },
        },
        "timestamp": 0,
    }

    # 1) Golpear la API
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(API_URL, json=payload_body)
        print(f"API status_code: {resp.status_code}")
        print(f"API body: {resp.json()}")

    if resp.status_code != 200:
        raise SystemExit(f"La API devolvió {resp.status_code}, abortando verificación en DB.")

    # 2) Verificar directamente en PostgreSQL
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        exec_row = await conn.fetchrow(
            "SELECT * FROM executions WHERE id = $1", uuid.UUID(execution_id)
        )
        event_row = await conn.fetchrow(
            "SELECT * FROM events WHERE execution_id = $1", uuid.UUID(execution_id)
        )

        print("\n--- Verificación en PostgreSQL ---")
        print("executions row:", dict(exec_row) if exec_row else None)
        print("events row:", dict(event_row) if event_row else None)

        asserts = []
        asserts.append(("executions tiene fila", exec_row is not None))
        asserts.append(("agent_name correcto", exec_row and exec_row["agent_name"] == "test_agent"))
        asserts.append(("status running o completed", exec_row and exec_row["status"] in ("running", "completed")))
        asserts.append(("events tiene fila", event_row is not None))
        asserts.append(("event_type tool_result", event_row and event_row["event_type"] == "tool_result"))

        for label, ok in asserts:
            print(f"  [{'OK' if ok else 'FAIL'}] {label}")
            if not ok:
                print(f"  -> Inspección directa en DB no coincidió para: {label}")

        all_ok = all(ok for _, ok in asserts)
        print(f"\nRESULTADO: {'TODAS LAS COMPROBACIONES OK' if all_ok else 'HUBO FALLOS'}")

    finally:
        # 3) Limpieza: borrar filas de prueba (ambas tablas)
        await conn.execute("DELETE FROM events WHERE execution_id = $1", uuid.UUID(execution_id))
        await conn.execute("DELETE FROM executions WHERE id = $1", uuid.UUID(execution_id))
        await conn.close()
        print("Limpieza de filas de prueba completada.")


if __name__ == "__main__":
    asyncio.run(main())

