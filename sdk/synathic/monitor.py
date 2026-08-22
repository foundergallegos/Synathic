import httpx
import time
from time import perf_counter
from typing import Optional

class SynathicMonitor:
    def __init__(self):
        self.api_key = None
        self.endpoint = "http://localhost:8000/api/events"
        self._client = None

    def start(self, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key
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

    async def send_event(self, execution_id: str, agent_name: str, event_type: str, payload: dict):
        if not self._client:
            return

        try:
            t0 = time.time()
            print(f"[monitor] send_event start {t0} endpoint={self.endpoint} event_type={event_type}")
            resp = await self._client.post(
                self.endpoint,
                json={
                    "execution_id": execution_id,
                    "agent_name": agent_name,
                    "event_type": event_type,
                    "payload": payload,
                    "timestamp": time.time()
                }
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

            # Momento justo antes de iniciar la llamada HTTP
            t_request_start = perf_counter()
            resp = await self._client.post(verify_url, json=req_body)
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

monitor = SynathicMonitor()