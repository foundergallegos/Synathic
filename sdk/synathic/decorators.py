import functools
import asyncio
import inspect
import uuid
from datetime import datetime
from .monitor import monitor

def expect(
    postcondition: str,
    table: str = None,
    match_field: str = None,
    expected_field: str = None,
    expected_value: str = None,
    sync: bool = False,
    timestamp_column: str | None = "updated_at",
):
    """Decorador para verificar una postcondicion tras ejecutar una herramienta.

    Tipos soportados por el backend:
      - "row_exists":     pass si existe una fila en `table` donde
                          `match_field = <valor de match_field en la llamada>`.
      - "row_not_exists": pass si NO existe tal fila.
      - "field_equals":   pass si ademas el valor real de `expected_field`
                          en esa fila es exactamente igual a `expected_value`.
    """
    def decorator(func):
        sig = inspect.signature(func)
        if match_field not in sig.parameters:
            raise TypeError(
                f"@expect: match_field={match_field!r} no es un parámetro de {func.__name__}. "
                f"Parámetros disponibles: {list(sig.parameters)}"
            )

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            execution_id = str(uuid.uuid4())
            execution_start = datetime.utcnow()

            # Para flujos síncronos evitamos enviar el `tool_call`.
            if not sync:
                # Fire-and-forget the tool_call to avoid adding network/DB latency
                try:
                    import asyncio as _asyncio
                    _asyncio.create_task(monitor.send_event(
                        execution_id=execution_id,
                        agent_name=func.__name__,
                        event_type="tool_call",
                        payload={"args": str(args), "kwargs": str(kwargs)}
                    ))
                except Exception:
                    # best-effort: if task creation fails, fall back to awaiting
                    await monitor.send_event(
                        execution_id=execution_id,
                        agent_name=func.__name__,
                        event_type="tool_call",
                        payload={"args": str(args), "kwargs": str(kwargs)}
                    )

            result = await func(*args, **kwargs)

            # Valor usado para localizar la fila: normalmente el argumento
            # cuyo nombre coincide con match_field.
            bound = inspect.signature(func).bind(*args, **kwargs)
            bound.apply_defaults()
            value = bound.arguments.get(match_field)
            if value is None:
                raise ValueError(
                    f"@expect: el valor de match_field={match_field!r} es None en esta llamada. "
                    "Verifica que el argumento se esté pasando correctamente."
                )

            postcondition_payload = {
                "type": postcondition,
                "table": table,
                "field": match_field,
                "value": value,
                "execution_start": execution_start.isoformat(),
                "timestamp_column": timestamp_column,
            }

            # Para field_equals, incluir el campo y el valor esperado extra.
            if postcondition == "field_equals":
                postcondition_payload["expected_field"] = expected_field or match_field
                postcondition_payload["expected_value"] = expected_value

            if monitor.db_dsn is not None:
                if sync:
                    verification_status = await monitor.verify_client_side(postcondition_payload)
                    await monitor.send_event(
                        execution_id=execution_id,
                        agent_name=func.__name__,
                        event_type="tool_result",
                        payload={
                            "result": str(result),
                            "postcondition": postcondition_payload,
                            "_skip_bg_verify": True,
                            "client_verification": verification_status,
                        },
                    )
                    verification = {"mode": "client", "status": verification_status}
                    if isinstance(result, dict):
                        result["verification"] = verification
                        return result
                    return {"result": result, "verification": verification}

                async def verify_and_report_client_side():
                    try:
                        verification_status = await monitor.verify_client_side(postcondition_payload)
                    except Exception as exc:
                        print(f"[monitor] client-side verification failed: {exc}")
                        verification_status = "unknown"
                    await monitor.send_event(
                        execution_id=execution_id,
                        agent_name=func.__name__,
                        event_type="tool_result",
                        payload={
                            "result": str(result),
                            "postcondition": postcondition_payload,
                            "_skip_bg_verify": True,
                            "client_verification": verification_status,
                        },
                    )

                try:
                    asyncio.create_task(verify_and_report_client_side())
                except Exception:
                    await verify_and_report_client_side()
                return result

            # Si sync=True, usar el método que espera la verificación síncrona
            if sync:
                verification = await monitor.send_event_and_wait_verification(
                    execution_id=execution_id,
                    agent_name=func.__name__,
                    event_type="tool_result",
                    payload={
                        "result": str(result),
                        "postcondition": postcondition_payload,
                    }
                )

                # Devolver el resultado junto con la verificación para que el
                # código llamante pueda tomar decisiones bloqueantes.
                if isinstance(result, dict):
                    result["verification"] = verification
                    return result
                return {"result": result, "verification": verification}
            else:
                await monitor.send_event(
                    execution_id=execution_id,
                    agent_name=func.__name__,
                    event_type="tool_result",
                    payload={
                        "result": str(result),
                        "postcondition": postcondition_payload,
                    }
                )

                return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
