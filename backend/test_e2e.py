import asyncio
import sys
import uuid

import asyncpg
import httpx

# Debe coincidir con la DATABASE_URL de database.py (sin el prefijo +asyncpg).
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"
API_URL = "http://localhost:8000/api"

# La tabla customers no es un modelo de SQLAlchemy, así que no la crea
# create_all. La creamos manualmente (IF NOT EXISTS) para poder verificar.
CREATE_CUSTOMERS = """
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    email TEXT,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
)
"""


async def call_event(
    client: httpx.AsyncClient,
    execution_id: str,
    value: str,
    post_type: str,
    expected_field: str = None,
    expected_value: str = None,
):
    """Simula un tool_result de un agente con una postcondition."""
    postcondition = {
        "type": post_type,
        "table": "customers",
        "field": "email",
        "value": value,
    }
    if post_type == "field_equals":
        postcondition["expected_field"] = expected_field or "name"
        postcondition["expected_value"] = expected_value

    payload = {
        "execution_id": execution_id,
        "agent_name": "e2e_test",
        "event_type": "tool_result",
        "payload": {
            "result": "success",
            "postcondition": postcondition,
        },
        "timestamp": 0,
    }
    return await client.post(f"{API_URL}/events", json=payload)


async def get_verifications(client: httpx.AsyncClient, execution_id: str):
    """Consulta GET /api/executions/{id} y devuelve la lista de verifications."""
    resp = await client.get(f"{API_URL}/executions/{execution_id}")
    resp.raise_for_status()
    data = resp.json()
    return data.get("verifications", [])


async def run_case(
    client: httpx.AsyncClient,
    conn: asyncpg.Connection,
    label: str,
    value: str,
    post_type: str,
    expected: str,
    insert_row: bool,
    name: str = None,
    expected_field: str = None,
    expected_value: str = None,
) -> bool:
    execution_id = str(uuid.uuid4())
    print(f"\n=== Caso: {label} (tipo={post_type}, esperado={expected}) ===")
    print(f"execution_id: {execution_id}")

    # 1) Opcionalmente insertar la fila de prueba en customers
    if insert_row:
        await conn.execute(
            "INSERT INTO customers (name, email) VALUES ($1, $2)",
            name or label,
            value,
        )
        print(f"Insertada fila en customers: email={value}, name={name or label}")

    # 2) Llamar al endpoint simulando un tool_result
    resp = await call_event(
        client,
        execution_id,
        value,
        post_type,
        expected_field=expected_field,
        expected_value=expected_value,
    )
    print(f"POST /api/events -> status {resp.status_code}, body {resp.json()}")
    if resp.status_code != 200:
        print(f"  [FAIL] El endpoint no devolvio 200")
        return False

    # 3) Esperar que corra el BackgroundTask de verificacion
    await asyncio.sleep(2)

    # 4) Consultar y confirmar por codigo el status de la verification
    verifications = await get_verifications(client, execution_id)
    print(f"GET /api/executions/{execution_id} -> verifications: {verifications}")

    if not verifications:
        print("  [FAIL] No se registro ninguna verification")
        return False

    status = verifications[0].get("status")
    ok = status == expected
    print(f"  [{'OK' if ok else 'FAIL'}] status de verification = {status} (esperado {expected})")

    # Limpieza de este caso
    try:
        await conn.execute(
            "DELETE FROM verifications WHERE execution_id = $1",
            uuid.UUID(execution_id),
        )
        await conn.execute(
            "DELETE FROM events WHERE execution_id = $1",
            uuid.UUID(execution_id),
        )
        await conn.execute(
            "DELETE FROM executions WHERE id = $1",
            uuid.UUID(execution_id),
        )
    except Exception as exc:
        print(f"  (aviso) fallo en limpieza: {exc}")

    return ok


async def main() -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    results = []
    try:
        # Asegurar que la tabla customers exista
        await conn.execute(CREATE_CUSTOMERS)
        print("Tabla customers asegurada (IF NOT EXISTS).")

        async with httpx.AsyncClient(timeout=10.0) as client:
            # --- row_exists ---
            # Caso 1: la fila SI existe -> "pass"
            results.append(
                await run_case(
                    client, conn,
                    label="row_exists_ok", value="exists@example.com",
                    post_type="row_exists", expected="pass",
                    insert_row=True,
                )
            )
            # Caso 2: la fila NO existe -> "fail"
            results.append(
                await run_case(
                    client, conn,
                    label="row_exists_fail", value="missing_row@example.com",
                    post_type="row_exists", expected="fail",
                    insert_row=False,
                )
            )

            # --- row_not_exists ---
            # Caso 3: la fila NO existe -> "pass"
            results.append(
                await run_case(
                    client, conn,
                    label="row_not_exists_ok", value="free_email@example.com",
                    post_type="row_not_exists", expected="pass",
                    insert_row=False,
                )
            )
            # Caso 4: la fila SI existe (no deberia) -> "fail"
            results.append(
                await run_case(
                    client, conn,
                    label="row_not_exists_fail", value="taken_email@example.com",
                    post_type="row_not_exists", expected="fail",
                    insert_row=True,
                )
            )

            # --- field_equals ---
            # Caso 5: fila existe y el campo coincide -> "pass"
            results.append(
                await run_case(
                    client, conn,
                    label="field_equals_ok", value="fe_ok@example.com",
                    post_type="field_equals", expected="pass",
                    insert_row=True, name="Alice",
                    expected_field="name", expected_value="Alice",
                )
            )
            # Caso 6: fila existe pero el campo NO coincide -> "fail"
            results.append(
                await run_case(
                    client, conn,
                    label="field_equals_fail", value="fe_bad@example.com",
                    post_type="field_equals", expected="fail",
                    insert_row=True, name="Alice",
                    expected_field="name", expected_value="Bob",
                )
            )

        # Limpieza final de filas de customers usadas en las pruebas
        for email in (
            "exists@example.com", "missing_row@example.com",
            "free_email@example.com", "taken_email@example.com",
            "fe_ok@example.com", "fe_bad@example.com",
        ):
            await conn.execute(
                "DELETE FROM customers WHERE email = $1", email
            )

    finally:
        await conn.close()

    print("\n================ RESUMEN ================")
    labels = [
        "row_exists ok", "row_exists fail",
        "row_not_exists ok", "row_not_exists fail",
        "field_equals ok", "field_equals fail",
    ]
    for idx, ok in enumerate(results, 1):
        print(f"  Caso {idx} ({labels[idx-1]}): {'PASS' if ok else 'FAIL'}")

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
