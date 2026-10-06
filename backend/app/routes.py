from types import SimpleNamespace
import os
from typing import Optional

from fastapi import APIRouter, Depends, BackgroundTasks, Request, Security, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import asyncpg
from .database import get_db, async_session, DATABASE_URL
from .models import Execution, Event, Verification
from .classifier import classify_failure

ASYNCPG_DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://") if DATABASE_URL.startswith("postgresql+asyncpg://") else DATABASE_URL
import uuid
from datetime import datetime
import time
from time import perf_counter

security = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    api_key = os.getenv("SYNATHIC_API_KEY")
    if not api_key:
        return
    if credentials is None or credentials.credentials != api_key:
        raise HTTPException(status_code=401, detail="API key inválida")


router = APIRouter(dependencies=[Depends(verify_api_key)])

TIMESTAMP_COLUMN_CACHE = {}

# Whitelist de tablas y columnas permitidas en verificación de postcondiciones.
# Los identificadores SQL no pueden parametrizarse, así que validamos los
# nombres contra esta whitelist para prevenir inyección SQL.
ALLOWED_TABLES = {
    "customers": {"id", "name", "email", "created_at", "updated_at"},
    "executions": {"id", "agent_name", "status", "started_at", "ended_at"},
    "events": {"id", "execution_id", "event_type", "payload", "timestamp"},
    "verifications": {
        "id", "execution_id", "postcondition_type", "target_table",
        "match_field", "match_value", "expected_field", "expected_value",
        "status", "checked_at",
    },
}

@router.post("/events")
async def create_event(
    data: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    execution_id = data.get("execution_id", str(uuid.uuid4()))
    event_type = data.get("event_type")

    # Buscar si la ejecución ya existe
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    if execution is None:
        # Primera vez que vemos este execution_id -> crearla
        execution = Execution(
            id=execution_id,
            agent_name=data.get("agent_name", "unknown"),
            status="running"
        )
        db.add(execution)

    # Guardar el evento
    event = Event(
        execution_id=execution_id,
        event_type=event_type,
        payload=data.get("payload", {})
    )
    db.add(event)

    # Si es el resultado final, marcar la ejecución como completada
    if event_type == "tool_result":
        execution.status = "completed"
        execution.ended_at = datetime.utcnow()

    try:
        await db.commit()
    except IntegrityError:
        # Condición de carrera: otra petición pudo haber creado la ejecución
        # al mismo tiempo. Hacemos rollback, recargamos la ejecución y
        # re-intentamos insertar solo el evento.
        await db.rollback()
        result = await db.execute(
            select(Execution).where(Execution.id == execution_id)
        )
        execution = result.scalar_one_or_none()

        # Recrear y persistir el evento ahora que la ejecución existe
        event = Event(
            execution_id=execution_id,
            event_type=event_type,
            payload=data.get("payload", {})
        )
        db.add(event)

        if event_type == "tool_result":
            execution.status = "completed"
            execution.ended_at = datetime.utcnow()

        await db.commit()

    if event_type == "tool_result":
        payload = data.get("payload", {}) or {}
        # Si el cliente indicó que quiere verificación síncrona, evitar
        # programar la verificación en background para prevenir duplicados.
        if not payload.get("_skip_bg_verify"):
            postcondition = payload.get("postcondition")
            if postcondition:
                background_tasks.add_task(
                    verify_postcondition,
                    execution_id,
                    postcondition
                )

    return {"status": "ok", "execution_id": execution_id}
    
@router.get("/executions")
async def list_executions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Execution))
    executions = result.scalars().all()
    return executions

@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, db: AsyncSession = Depends(get_db)):
    await _ensure_verification_schema(db)
    await db.commit()

    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    events_result = await db.execute(
        select(Event).where(Event.execution_id == execution_id).order_by(Event.timestamp)
    )
    events = events_result.scalars().all()

    verif_result = await db.execute(
        select(Verification).where(Verification.execution_id == execution_id)
    )
    verifications = verif_result.scalars().all()

    return {
        "execution": execution,
        "events": events,
        "verifications": verifications
    }

async def _ensure_verification_schema(session: AsyncSession):
    try:
        await session.execute(text("ALTER TABLE IF EXISTS verifications ADD COLUMN IF NOT EXISTS error_message TEXT"))
        await session.execute(text("ALTER TABLE IF EXISTS verifications ADD COLUMN IF NOT EXISTS failure_category VARCHAR"))
    except Exception:
        pass


async def _record_verification_exception(session: AsyncSession, execution_id: str, postcondition: dict, exc: Exception):
    if not isinstance(postcondition, dict):
        postcondition = {}

    try:
        await session.rollback()
    except Exception:
        pass

    await _ensure_verification_schema(session)

    raw_payload = {
        "id": uuid.uuid4(),
        "execution_id": execution_id,
        "postcondition_type": postcondition.get("type", "unknown"),
        "target_table": postcondition.get("table"),
        "match_field": postcondition.get("field"),
        "match_value": str(postcondition.get("value")) if postcondition.get("value") is not None else None,
        "expected_field": postcondition.get("expected_field"),
        "expected_value": str(postcondition.get("expected_value")) if postcondition.get("expected_value") is not None else None,
        "status": "unknown",
        "checked_at": datetime.utcnow(),
        "error_message": str(exc),
    }

    try:
        await session.execute(
            text("""
                INSERT INTO verifications (
                    id, execution_id, postcondition_type, target_table, match_field,
                    match_value, expected_field, expected_value, status,
                    checked_at, error_message
                ) VALUES (
                    :id, :execution_id, :postcondition_type, :target_table, :match_field,
                    :match_value, :expected_field, :expected_value, :status,
                    :checked_at, :error_message
                )
            """),
            raw_payload,
        )
        await session.commit()
        return
    except Exception as inner:
        print(f"[verify] failed to record unknown verification: {inner}")
        try:
            await session.rollback()
        except Exception:
            pass

    try:
        conn = await asyncpg.connect(ASYNCPG_DATABASE_URL)
        try:
            await conn.execute("ALTER TABLE IF EXISTS verifications ADD COLUMN IF NOT EXISTS error_message TEXT")
            await conn.execute("ALTER TABLE IF EXISTS verifications ADD COLUMN IF NOT EXISTS failure_category VARCHAR")
            await conn.execute(
                """
                INSERT INTO verifications (
                    id, execution_id, postcondition_type, target_table, match_field,
                    match_value, expected_field, expected_value, status,
                    checked_at, error_message
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
                )
                """,
                raw_payload["id"],
                raw_payload["execution_id"],
                raw_payload["postcondition_type"],
                raw_payload["target_table"],
                raw_payload["match_field"],
                raw_payload["match_value"],
                raw_payload["expected_field"],
                raw_payload["expected_value"],
                raw_payload["status"],
                raw_payload["checked_at"],
                raw_payload["error_message"],
            )
        finally:
            await conn.close()
    except Exception as final_inner:
        print(f"[verify] direct asyncpg fallback failed: {final_inner}")


async def verify_postcondition(execution_id: str, postcondition: dict):
    # Mantener compatibilidad: abrir una sesión y delegar en la función común
    try:
        async with async_session() as session:
            # Ignoramos los timings en la ejecución en background
            await _perform_verification(session, execution_id, postcondition)
    except Exception as e:
        async with async_session() as session:
            await _record_verification_exception(session, execution_id, postcondition, e)
        print(f"Verification error: {e}")


async def _perform_verification(session: AsyncSession, execution_id: str, postcondition: dict):
    """Realiza la verificación usando la sesión proporcionada y devuelve el status
    y un desglose de timings. Devuelve (status, timings_dict)."""
    try:
        await _ensure_verification_schema(session)

        t_entry = perf_counter()
        print(f"[verify] start {_short_ts()} execution_id={execution_id}")
        post_type = postcondition.get("type")
        table = postcondition.get("table")
        field = postcondition.get("field")
        value = postcondition.get("value")
        timestamp_column = postcondition.get("timestamp_column", "updated_at")
        execution_start_raw = postcondition.get("execution_start")

        if isinstance(execution_start_raw, str):
            execution_start = datetime.fromisoformat(execution_start_raw.replace("Z", "+00:00"))
        else:
            execution_start = execution_start_raw or datetime.utcnow()
        if execution_start.tzinfo is not None:
            execution_start = execution_start.astimezone().replace(tzinfo=None)

        if table not in ALLOWED_TABLES:
            raise ValueError(f"Tabla no permitida: {table}")
        if field not in ALLOWED_TABLES[table]:
            raise ValueError(f"Columna no permitida en {table}: {field}")
        if timestamp_column is not None:
            if timestamp_column not in ALLOWED_TABLES[table]:
                raise ValueError(f"Columna no permitida en {table}: {timestamp_column}")
            cache_key = (table, timestamp_column)
            if cache_key not in TIMESTAMP_COLUMN_CACHE:
                column_check = text(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = :table_name AND column_name = :column_name LIMIT 1"
                )
                has_timestamp = (await session.execute(column_check, {
                    "table_name": table,
                    "column_name": timestamp_column,
                })).first()
                TIMESTAMP_COLUMN_CACHE[cache_key] = has_timestamp is not None
            if not TIMESTAMP_COLUMN_CACHE[cache_key]:
                raise ValueError(
                    f"timestamp_column '{timestamp_column}' not found on table '{table}', either add the column or pass timestamp_column=None to explicitly opt out of freshness checking"
                )

        status = "fail"
        timings = {}

        if value is None:
            raise ValueError("postcondition value cannot be None — check that match_field is being passed correctly from the SDK")

        if post_type in ("row_exists", "row_not_exists"):
            print(f"[verify] before SELECT {_short_ts()} table={table} field={field} value={value} timestamp_column={timestamp_column}")
            t_db_start = perf_counter()
            if timestamp_column is None:
                query = text(f"SELECT 1 FROM {table} WHERE {field} = :value")
                row = (await session.execute(query, {"value": value})).first()
            else:
                query = text(f"SELECT 1 FROM {table} WHERE {field} = :value AND {timestamp_column} > :execution_start")
                row = (await session.execute(query, {
                    "value": value,
                    "execution_start": execution_start,
                })).first()
            t_db_end = perf_counter()
            db_select_ms = (t_db_end - t_db_start) * 1000
            timings["db_select_ms"] = db_select_ms
            print(f"[verify] after SELECT {_short_ts()} elapsed_ms={db_select_ms:.1f}")

            if post_type == "row_exists":
                status = "pass" if row else "fail"
            else:
                status = "pass" if not row else "fail"

            verification = Verification(
                execution_id=execution_id,
                postcondition_type=post_type,
                target_table=table,
                match_field=field,
                match_value=str(value),
                status=status,
                checked_at=datetime.utcnow(),
            )
            session.add(verification)
            if status == "fail":
                try:
                    result = classify_failure(execution_id, postcondition, None)
                    verification.failure_category = result["category"]
                except Exception as exc:
                    print(f"[classify] error: {exc}")
                    verification.failure_category = None
            t_commit_start = perf_counter()
            await session.commit()
            t_commit_end = perf_counter()
            timings["commit_ms"] = (t_commit_end - t_commit_start) * 1000
            print(f"[verify] commit elapsed_ms={timings['commit_ms']:.1f} {_short_ts()}")

        elif post_type == "field_equals":
            expected_field = postcondition.get("expected_field")
            expected_value = postcondition.get("expected_value")

            if expected_field not in ALLOWED_TABLES[table]:
                raise ValueError(f"Columna no permitida en {table}: {expected_field}")

            print(f"[verify] before SELECT field_equals {_short_ts()} table={table} field={field} expected_field={expected_field} value={value} timestamp_column={timestamp_column}")
            t_db_start = perf_counter()
            if timestamp_column is None:
                query = text(f"SELECT {expected_field} FROM {table} WHERE {field} = :value LIMIT 1")
                row = (await session.execute(query, {"value": value})).first()
            else:
                query = text(f"SELECT {expected_field} FROM {table} WHERE {field} = :value AND {timestamp_column} > :execution_start LIMIT 1")
                row = (await session.execute(query, {
                    "value": value,
                    "execution_start": execution_start,
                })).first()
            t_db_end = perf_counter()
            timings["db_select_ms"] = (t_db_end - t_db_start) * 1000
            print(f"[verify] after SELECT field_equals {_short_ts()} elapsed_ms={timings['db_select_ms']:.1f}")

            status = "fail"
            if row is not None:
                actual = row[0]
                if expected_value is not None:
                    status = "pass" if str(actual) == str(expected_value) else "fail"
                else:
                    status = "pass"

            verification = Verification(
                execution_id=execution_id,
                postcondition_type="field_equals",
                target_table=table,
                match_field=field,
                match_value=str(value),
                expected_field=expected_field,
                expected_value=str(expected_value) if expected_value is not None else None,
                status=status,
                checked_at=datetime.utcnow(),
            )
            session.add(verification)
            if status == "fail":
                actual_row = {expected_field: row[0]} if row is not None else None
                try:
                    result = classify_failure(execution_id, postcondition, actual_row)
                    verification.failure_category = result["category"]
                except Exception as exc:
                    print(f"[classify] error: {exc}")
                    verification.failure_category = None
            t_commit_start = perf_counter()
            await session.commit()
            t_commit_end = perf_counter()
            timings["commit_ms"] = (t_commit_end - t_commit_start) * 1000
            print(f"[verify] commit elapsed_ms={timings['commit_ms']:.1f} {_short_ts()}")

        else:
            print(f"Tipo de postcondition no soportado: {post_type}")

        t_exit = perf_counter()
        timings["total_server_ms"] = (t_exit - t_entry) * 1000
        print(f"[verify] end {_short_ts()} execution_id={execution_id} status={status}")
        return status, timings
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        raise


def _short_ts():
    return datetime.utcnow().isoformat(timespec='milliseconds')


@router.post("/verify-sync")
async def verify_sync(data: dict, db: AsyncSession = Depends(get_db)):
    """Endpoint síncrono para verificar postconditions y devolver el resultado inmediatamente."""
    execution_id = data.get("execution_id")
    postcondition = data.get("postcondition")
    if not postcondition:
        return {"status": "error", "reason": "no postcondition provided"}

    try:
        status, timings = await _perform_verification(db, execution_id, postcondition)
        return {"status": status, "timings": timings}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": "invalid_postcondition", "message": str(e)})
    except Exception as e:
        await _record_verification_exception(db, execution_id, postcondition, e)
        return JSONResponse(status_code=500, content={"error": "verification_error", "message": str(e)})


@router.post("/events-verify", response_model=None)
async def events_verify(data: dict, request: Request = None, db: AsyncSession = Depends(get_db)):
    """Endpoint combinado: inserta el evento y realiza la verificación en la misma
    petición, devolviendo el resultado y un desglose de timings del servidor."""
    t_recv = perf_counter()
    t_handler_start = perf_counter()
    if request is None:
        request = SimpleNamespace(state=SimpleNamespace())
    request.state.synathic_request_received_at = t_recv
    request.state.synathic_handler_start = t_handler_start
    execution_id = data.get("execution_id", str(uuid.uuid4()))
    event_type = data.get("event_type")

    # A partir de aquí usamos la sesión proporcionada por Depends
    # Crear la ejecución si no existe
    result = await db.execute(select(Execution).where(Execution.id == execution_id))
    execution = result.scalar_one_or_none()
    if execution is None:
        execution = Execution(
            id=execution_id,
            agent_name=data.get("agent_name", "unknown"),
            status="running"
        )
        db.add(execution)

    # Insertar el evento
    payload = data.get("payload", {}) or {}
    event = Event(
        execution_id=execution_id,
        event_type=event_type,
        payload=payload
    )
    db.add(event)

    if event_type == "tool_result":
        execution.status = "completed"
        execution.ended_at = datetime.utcnow()

    # Hacer flush/commit de evento y ejecución antes de la verificación
    t_before_db_commit = perf_counter()
    try:
        await db.commit()
    except IntegrityError:
        # Manejar condición de carrera similar a create_event
        await db.rollback()
        result = await db.execute(select(Execution).where(Execution.id == execution_id))
        execution = result.scalar_one_or_none()
        # Reinsertar evento
        event = Event(
            execution_id=execution_id,
            event_type=event_type,
            payload=payload
        )
        db.add(event)
        if event_type == "tool_result":
            execution.status = "completed"
            execution.ended_at = datetime.utcnow()
        await db.commit()
    t_after_db_commit = perf_counter()

    # Ejecutar verificación (en la misma sesión)
    postcondition = payload.get("postcondition") if isinstance(payload, dict) else None
    verify_result = None
    verify_timings = {}
    if event_type == "tool_result" and postcondition:
        try:
            status, timings = await _perform_verification(db, execution_id, postcondition)
            verify_result = status
            verify_timings = timings
        except ValueError as e:
            # Normalizar errores de validación de postconditions a 400 Bad Request
            msg = str(e)
            if "Tabla no permitida" in msg or "Tabla no permitida" in msg:
                err_code = "table_not_allowed"
            elif "Columna no permitida" in msg:
                err_code = "column_not_allowed"
            else:
                err_code = "invalid_postcondition"
            return JSONResponse(status_code=400, content={
                "error": err_code,
                "message": msg
            })
        except Exception as e:
            await _record_verification_exception(db, execution_id, postcondition, e)
            return JSONResponse(status_code=500, content={
                "error": "verification_error",
                "message": str(e)
            })

    t_done = perf_counter()

    request_received_to_handler_ms = (t_handler_start - t_recv) * 1000
    pool_wait_ms = getattr(request.state, "synathic_pool_wait_ms", 0.0)
    if request is None:
        pool_wait_ms = 0.0
    server_rest_ms = max(0.0, (t_done - t_handler_start) * 1000 - pool_wait_ms)

    server_timings = {
        "server_received_to_db_commit_ms": (t_after_db_commit - t_recv) * 1000,
        "db_commit_ms": (t_after_db_commit - t_before_db_commit) * 1000,
        "request_received_to_handler_ms": request_received_to_handler_ms,
        "pool_wait_ms": pool_wait_ms,
        "rest_server_ms": server_rest_ms,
        "server_total_ms": (t_done - t_recv) * 1000,
    }

    # Combinar timings con los de verificación (si existen)
    server_timings.update({"verify": verify_timings})

    return {"status": verify_result, "server_timings": server_timings}
