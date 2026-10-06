import httpx
import time
import re
from datetime import datetime
from time import perf_counter
from typing import Optional

_SQL_IDENTIFIER = re.compile(r"^[a-z_][a-zA-Z0-9_]*$")

class SynathicMonitor:
    def __init__(self):
        self.api_key = None
        self.endpoint = "http://localhost:8000/api/events"
        self._client = None
        self.db_dsn = None
        self.demo_mode = False

    def start(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        db_dsn: Optional[str] = None,
        demo_mode: bool = False,
    ):
        if db_dsn is None and not demo_mode:
            raise ValueError("monitor.start() requiere db_dsn='...' o demo_mode=True")
        if db_dsn is not None and demo_mode:
            raise ValueError("db_dsn y demo_mode=True son mutuamente excluyentes")

        self.api_key = api_key
        self.db_dsn = db_dsn
        self.demo_mode = demo_mode
        if endpoint:
            self.endpoint = endpoint
        # Crear el client asíncrono que se reutiliza en llamadas posteriores
        self._client = httpx.AsyncClient(timeout=5.0)

        # Warmup no bloqueante: lanzar ping a /health en background (no espera)
        try:
            import threading
            def _bg_ping(url: str):
                try:
                    with httpx.Client(timeout=1.0) as c:
                        c.get(url)
                except Exception:
                    pass

            warm_url = self.endpoint.replace('/events', '/health')
            t = threading.Thread(target=_bg_ping, args=(warm_url,), daemon=True)
            t.start()
        except Exception:
            pass

        print("Synathic monitor started")

    async def verify_client_side(self, postcondition: dict) -> str:
        """Verify a postcondition directly against the customer's PostgreSQL."""
        if self.db_dsn is None:
            raise RuntimeError("client-side verification requires db_dsn")

        post_type = postcondition.get("type")
        table = postcondition.get("table")
        field = postcondition.get("field")
        timestamp_column = postcondition.get("timestamp_column", "updated_at")
        expected_field = postcondition.get("expected_field")

        identifiers = [table, field]
        if timestamp_column is not None:
            identifiers.append(timestamp_column)
        if post_type == "field_equals":
            identifiers.append(expected_field)

        for identifier in identifiers:
            if not isinstance(identifier, str) or not _SQL_IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"identificador SQL inválido: {identifier}")

        if post_type not in {"row_exists", "row_not_exists", "field_equals"}:
            raise ValueError(f"postcondition type not supported: {post_type}")

        value = postcondition.get("value")
        if value is None:
            raise ValueError("postcondition value cannot be None")

        query = f"SELECT {expected_field if post_type == 'field_equals' else '1'} FROM {table} WHERE {field} = $1"
        parameters = [value]
        if timestamp_column is not None:
            execution_start = postcondition.get("execution_start")
            if isinstance(execution_start, str):
                execution_start = execution_start.replace("Z", "+00:00")
                execution_start = datetime.fromisoformat(execution_start)
            if execution_start is None:
                execution_start = datetime.utcnow()
            query += f" AND {timestamp_column} > $2"
            parameters.append(execution_start)
        if post_type == "field_equals":
            query += " LIMIT 1"

        import asyncpg

        connection = None
        try:
            try:
                connection = await asyncpg.connect(self.db_dsn)
            except Exception:
                print("error de conexión al DB del cliente")
                raise RuntimeError("error de conexión al DB del cliente") from None

            row = await connection.fetchrow(query, *parameters)
            if post_type == "row_exists":
                return "pass" if row is not None else "fail"
            if post_type == "row_not_exists":
                return "pass" if row is None else "fail"

            if row is None:
                return "fail"
            expected_value = postcondition.get("expected_value")
            if expected_value is None:
                return "pass"
            return "pass" if str(row[0]) == str(expected_value) else "fail"
        finally:
            if connection is not None:
                await connection.close()

    async def send_event(self, execution_id: str, agent_name: str, event_type: str, payload: dict):
        if not self._client:
            return

        try:
            t0 = time.time()
            print(f"[monitor] send_event start {t0} endpoint={self.endpoint} event_type={event_type}")
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key is not None else None
            resp = await self._client.post(
                self.endpoint,
                json={
                    "execution_id": execution_id,
                    "agent_name": agent_name,
                    "event_type": event_type,
                    "payload": payload,
                    "timestamp": time.time()
                },
                headers=headers,
            )
            t1 = time.time()
            print(f"[monitor] send_event done elapsed_ms={(t1-t0)*1000:.1f} event_type={event_type}")
        except Exception:
            # Fire-and-forget: si el backend está caído, el agente sigue corriendo
            pass

    async def send_event_and_wait_verification(self, execution_id: str, agent_name: str, event_type: str, payload: dict):
        """Enviar el evento y, si contiene una postcondition, pedir verificación síncrona al backend.
        Devuelve el JSON de la respuesta del endpoint /api/events-verify cuando aplique.
        """
        # Enviar un único request que inserta el evento y ejecuta la verificación
        if not self._client:
            return None

        try:
            timings = {}
            t_wrapper_start = perf_counter()

            client_created = False
            if getattr(self, "_client", None) is None:
                t_client_create_start = perf_counter()
                self._client = httpx.AsyncClient(timeout=5.0)
                t_client_create_end = perf_counter()
                timings["client_create_ms"] = (t_client_create_end - t_client_create_start) * 1000
                client_created = True

            # Preparar payload y endpoint nuevo `/events-verify`
            verify_url = self.endpoint.replace('/events', '/events-verify')
            req_body = {
                "execution_id": execution_id,
                "agent_name": agent_name,
                "event_type": event_type,
                "payload": payload,
                "timestamp": time.time()
            }
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key is not None else None

            # Momento justo antes de iniciar la llamada HTTP
            t_request_start = perf_counter()
            resp = await self._client.post(verify_url, json=req_body, headers=headers)
            t_response_received = perf_counter()

            timings["func_to_request_start_ms"] = (t_request_start - t_wrapper_start) * 1000
            timings["request_roundtrip_ms"] = (t_response_received - t_request_start) * 1000

            # Intentar parsear la respuesta que incluye timings del servidor
            try:
                j = resp.json()
            except Exception:
                j = None

            # Incluir measurementes del lado servidor si están presentes
            result = {"client_timings": timings, "server_response": j}
            return result
        except Exception:
            return None

    async def send_event_optional(self, execution_id: str, agent_name: str, event_type: str, payload: dict):
        """Send an optional best-effort event, including configured API auth."""
        await self.send_event(execution_id, agent_name, event_type, payload)

monitor = SynathicMonitor()