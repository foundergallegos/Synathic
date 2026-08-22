"""
Clasificador determinístico de fallos de verificación.

Cuando una ``Verification`` termina con status ``"fail"``, este módulo intenta
diagnosticar la causa probable comparando timestamps y metadata, usando
SOLO reglas simples y deterministas. No emplea ningún modelo de IA.

Categorías devueltas por ``classify_failure``:
    row_never_inserted
        La fila que la postcondición esperaba encontrar (o NO esperaba) no
        apareció / apareció donde no debía, y no hay evidencia de una carrera.
    race_condition
        La fila se insertó (o se modificó) DESPUÉS del instante en que la
        verificación ya había mirado la base de datos -> la comprobación corrió
        antes de que el dato estuviera disponible.
    value_mismatch
        La fila sí existe, pero el valor real del campo comparado difiere del
        esperado (aunque es "parecido": probablemente un typo o diferencia de
        mayúsculas / espacios).
    format_mismatch
        La fila sí existe, pero el valor real difiere del esperado por un tema
        de TIPO o FORMATO (p.ej. int vs str, "2026-01-01" vs
        "2026-01-01T00:00:00", "1.0" vs "1", timestamps).
    stale_row
        La postcondición era ``row_not_exists`` y la fila YA existía en la base
        antes de la verificación (dato viejo / residuo, no una carrera).
    unclassified
        Ninguna regla aplica, o falta metadata suficiente para decidir.

El resultado es explicable: se incluye una lista de ``reasons`` (las reglas que
se dispararon) y un bloque ``evidence`` con los datos comparados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Umbrales configurables (reglas deterministas)
# ---------------------------------------------------------------------------

# "Parecido" de strings para sospechar typo / espacios / mayúsculas (0..1).
VALUE_SIMILARITY_RATIO = 0.8

# Margen (en segundos) para decidir si una inserción es "posterior" al
# instante de la verificación.
RACE_MARGIN_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Helpers de datos
# ---------------------------------------------------------------------------


@dataclass
class Events:
    """Timestamps extraídos de la metadata, todos en epoch (float) o None."""

    event_at: Optional[float] = None        # cuándo llegó el tool_result
    checked_at: Optional[float] = None      # cuándo corrió la verificación
    row_inserted_at: Optional[float] = None  # cuándo se insertó la fila
    row_updated_at: Optional[float] = None   # cuándo se modificó la fila


def _to_epoch(value: Any) -> Optional[float]:
    """Convierte datetime / ISO-8601 str / epoch numérico a epoch float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        # Heurística: >1e12 suele ser milisegundos.
        return num / 1000.0 if num > 1_000_000_000_000 else num
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _extract_events(
    execution_id: str,
    postcondition: Dict[str, Any],
    actual_row: Optional[Dict[str, Any]],
) -> Events:
    """
    Extrae timestamps desde (por orden de prioridad):
      1. ``postcondition["_meta"]``   -> event_at / checked_at /
                                         row_inserted_at / row_updated_at
      2. ``actual_row``              -> columnas created_at / inserted_at /
                                         timestamp / updated_at / modified_at
      3. postcondition directo       -> event_at / checked_at
    """
    meta = postcondition.get("_meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    ev = Events(
        event_at=_to_epoch(meta.get("event_at")),
        checked_at=_to_epoch(meta.get("checked_at")),
        row_inserted_at=_to_epoch(meta.get("row_inserted_at")),
        row_updated_at=_to_epoch(meta.get("row_updated_at")),
    )

    if ev.event_at is None:
        ev.event_at = _to_epoch(postcondition.get("event_at"))
    if ev.checked_at is None:
        ev.checked_at = _to_epoch(postcondition.get("checked_at"))

    if isinstance(actual_row, dict):
        for key in ("created_at", "inserted_at", "timestamp"):
            if ev.row_inserted_at is None:
                ev.row_inserted_at = _to_epoch(actual_row.get(key))
        for key in ("updated_at", "modified_at"):
            if ev.row_updated_at is None:
                ev.row_updated_at = _to_epoch(actual_row.get(key))

    return ev


def _norm(value: Any) -> str:
    """Normaliza un valor para comparaciones de typo/formato."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    return re.sub(r"\s+", " ", s)


def _is_pretty_similar(a: Any, b: Any) -> bool:
    """Comparación indulgente: mayúsculas, espacios y ratio difflib."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    ratio = SequenceMatcher(None, na, nb).ratio()
    return ratio >= VALUE_SIMILARITY_RATIO


def _type_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, dict):
        return "json"
    if isinstance(value, (list, tuple)):
        return "list"
    return "string"


def _is_format_issue(actual: Any, expected: Any) -> bool:
    """Distintos tipos/formatos pero contenido esencialmente igual."""
    try:
        if _type_kind(actual) == "number" and _type_kind(expected) == "string":
            float(str(expected))
            return True
        if _type_kind(expected) == "number" and _type_kind(actual) == "string":
            float(str(actual))
            return True
    except (TypeError, ValueError):
        pass

    ts_a = _to_epoch(actual)
    ts_b = _to_epoch(expected)
    if ts_a is not None and ts_b is not None:
        return abs(ts_a - ts_b) < RACE_MARGIN_SECONDS

    return False


def _has_key(d: Dict[str, Any], key: str) -> bool:
    return any(str(k).lower() == key for k in d)


# ---------------------------------------------------------------------------
# Reglas del clasificador
# ---------------------------------------------------------------------------


def _classify_row_presence(
    post_type: str,
    post_field: str,
    post_value: Any,
    actual_row: Optional[Dict[str, Any]],
    events: Events,
    reasons: list,
) -> Optional[str]:
    """Reglas para row_exists / row_not_exists según presencia y timestamps."""
    row_found = isinstance(actual_row, dict) and len(actual_row) > 0

    if post_type == "row_exists":
        if not row_found:
            if (
                events.row_inserted_at is not None
                and events.checked_at is not None
                and events.row_inserted_at > events.checked_at + RACE_MARGIN_SECONDS
            ):
                reasons.append(
                    "row_exists: fila insertada después del checked_at -> carrera"
                )
                return "race_condition"
            if events.row_inserted_at is None:
                reasons.append(
                    "row_exists: sin evidencia de inserción posterior -> nunca se insertó"
                )
                return "row_never_inserted"
            reasons.append(
                "row_exists: sin fila en el momento de la verificación"
            )
            return "row_never_inserted"
        if (
            events.row_inserted_at is not None
            and events.checked_at is not None
            and events.row_inserted_at > events.checked_at - RACE_MARGIN_SECONDS
        ):
            reasons.append(
                "row_exists: fila presente pero insertada cerca/después del "
                "checked_at -> carrera"
            )
            return "race_condition"
        reasons.append(
            "row_exists: fila presente ahora pero verificación fallida; sin "
            "timeout claro -> carrera probable"
        )
        return "race_condition"

    # row_not_exists: esperábamos que NO existiera, pero existe.
    if row_found:
        if (
            events.row_inserted_at is not None
            and events.checked_at is not None
            and events.checked_at - RACE_MARGIN_SECONDS
            <= events.row_inserted_at
            <= events.checked_at + RACE_MARGIN_SECONDS
        ):
            reasons.append(
                "row_not_exists: fila insertada poco antes del checked_at -> carrera"
            )
            return "race_condition"
        reasons.append(
            "row_not_exists: fila YA existía (dato previo/residual) -> stale_row"
        )
        return "stale_row"
    return None


def _classify_field_equals(
    post_field: str,
    post_value: Any,
    expected_field: Any,
    expected_value: Any,
    actual_row: Optional[Dict[str, Any]],
    events: Events,
    reasons: list,
) -> Optional[str]:
    """Reglas para field_equals: la fila debe existir y su campo coincidir."""
    row_found = isinstance(actual_row, dict) and len(actual_row) > 0

    if not row_found:
        if (
            events.row_inserted_at is not None
            and events.checked_at is not None
            and events.row_inserted_at > events.checked_at + RACE_MARGIN_SECONDS
        ):
            reasons.append("field_equals: fila insertada tras el check -> carrera")
            return "race_condition"
        reasons.append(
            "field_equals: la fila de localización no existe -> nunca se insertó"
        )
        return "row_never_inserted"

    # La fila fue modificada después de la verificación.
    if (
        events.row_updated_at is not None
        and events.checked_at is not None
        and events.row_updated_at > events.checked_at - RACE_MARGIN_SECONDS
    ):
        reasons.append(
            "field_equals: fila modificada cerca/después del checked_at -> carrera"
        )
        return "race_condition"

    # Extraer el valor real del campo seleccionado.
    actual: Any = None
    cell_found = False
    if expected_field is not None and isinstance(actual_row, dict):
        for key, cand in actual_row.items():
            if str(key).lower() == str(expected_field).lower():
                actual, cell_found = cand, True
                break
        if not cell_found:
            actual = actual_row.get("actual_value")
            cell_found = actual_row.get("actual_value") is not None or _has_key(
                actual_row, "actual_value"
            )

    if not cell_found:
        reasons.append(
            "field_equals: no se pudo localizar la celda esperada en actual_row"
        )
        return "unclassified"

    # Comparación de valor.
    if str(actual) == str(expected_value):
        # Textualmente iguales pero de tipos distintos -> problema de formato.
        if _type_kind(actual) != _type_kind(expected_value):
            reasons.append(
                "field_equals: valores textualmente iguales pero tipos "
                "distintos -> format_mismatch"
            )
            return "format_mismatch"
        reasons.append("field_equals: valores iguales")
        return "unclassified"

    if _is_pretty_similar(actual, expected_value):
        reasons.append(
            "field_equals: valores parecidos "
            "(typo / mayúsculas / espacios) -> value_mismatch"
        )
        return "value_mismatch"

    if _is_format_issue(actual, expected_value):
        reasons.append(
            "field_equals: diferencia de tipo/formato del valor -> format_mismatch"
        )
        return "format_mismatch"

    reasons.append(
        "field_equals: el valor real simplemente no coincide -> value_mismatch"
    )
    return "value_mismatch"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def classify_failure(
    execution_id: str,
    postcondition: Dict[str, Any],
    actual_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Clasifica de forma determinista la causa probable de una verificación que
    terminó en "fail".

    Args:
        execution_id: ID de la ejecución (solo informativo / trazabilidad).
        postcondition: dict con la postcondición tal como llegó en el evento.
        actual_row: dict con la fila real obtenida de la BD (o ``None`` si la
            verificación no encontró la fila). Puede incluir timestamps
            (created_at / updated_at) y la celda comparada.

    Returns:
        dict con:
          - "category": str (ver constante documentada arriba).
          - "execution_id": el id pasado.
          - "postcondition_type": tipo de postcondición.
          - "reasons": lista de reglas disparadas (explicabilidad).
          - "evidence": timestamps y valores comparados.
    """
    category = "unclassified"
    reasons: list = []
    post_type = postcondition.get("type")

    field = postcondition.get("field")
    value = postcondition.get("value")
    expected_field = postcondition.get("expected_field")
    expected_value = postcondition.get("expected_value")

    events = _extract_events(execution_id, postcondition, actual_row)

    if post_type in ("row_exists", "row_not_exists"):
        category = _classify_row_presence(
            post_type, field, value, actual_row, events, reasons
        )
    elif post_type == "field_equals":
        category = _classify_field_equals(
            field, value, expected_field, expected_value,
            actual_row, events, reasons,
        )
    else:
        reasons.append(f"Tipo de postcondición no soportado: {post_type!r}")

    if category is None:
        category = "unclassified"
        reasons.append("Ninguna regla aplica con la metadata disponible")

    return {
        "category": category,
        "execution_id": str(execution_id),
        "postcondition_type": post_type,
        "reasons": reasons,
        "evidence": {
            "event_at": events.event_at,
            "checked_at": events.checked_at,
            "row_inserted_at": events.row_inserted_at,
            "row_updated_at": events.row_updated_at,
            "post_field": field,
            "post_value": value,
            "expected_field": expected_field,
            "expected_value": expected_value,
        },
    }


# ---------------------------------------------------------------------------
# Smoke test integrado (python backend/app/classifier.py)
# ---------------------------------------------------------------------------


def _demo() -> None:
    def t(label, result):
        print(f"  {label:34s} -> {result['category']}")
        for r in result["reasons"]:
            print(f"       - {r}")

    print("=== Demo classifier ===")
    t(
        "row_exists (no row)",
        classify_failure(
            "aaa",
            {"type": "row_exists", "table": "customers",
             "field": "email", "value": "x@e.com"},
            None,
        ),
    )
    t(
        "row_exists (late insert)",
        classify_failure(
            "bbb",
            {"type": "row_exists", "field": "email", "value": "x@e.com",
             "_meta": {"checked_at": "2026-01-01T00:00:00+00:00"}},
            {"email": "x@e.com", "created_at": "2026-01-01T00:00:10+00:00"},
        ),
    )
    t(
        "row_not_exists (stale)",
        classify_failure(
            "ccc",
            {"type": "row_not_exists", "field": "email", "value": "x@e.com",
             "_meta": {"checked_at": "2026-01-01T00:00:00+00:00"}},
            {"email": "x@e.com", "created_at": "2025-01-01T00:00:00+00:00"},
        ),
    )
    t(
        "field_equals (typo)",
        classify_failure(
            "ddd",
            {"type": "field_equals", "field": "email", "value": "x@e.com",
             "expected_field": "name", "expected_value": "Alice"},
            {"email": "x@e.com", "name": "alice"},
        ),
    )
    t(
        "field_equals (format)",
        classify_failure(
            "eee",
            {"type": "field_equals", "field": "id", "value": "x@e.com",
             "expected_field": "status", "expected_value": "1"},
            {"id": "x@e.com", "status": 1},
        ),
    )
    t(
        "field_equals (totally different)",
        classify_failure(
            "fff",
            {"type": "field_equals", "field": "email", "value": "x@e.com",
             "expected_field": "name", "expected_value": "Robert"},
            {"email": "x@e.com", "name": "Alice"},
        ),
    )


if __name__ == "__main__":
    _demo()

