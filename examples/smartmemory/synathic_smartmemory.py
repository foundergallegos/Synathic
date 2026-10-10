"""Adapter: Synathic verification failures -> SmartMemory failure journal.

Synathic 0.1.2 facts this adapter relies on (decorators.py, monitor.py):
- The decorated function MUST be a coroutine. A plain function gets sync_wrapper
  and bypasses verification entirely (decorators.py:164-170).
- With monitor.start(db_dsn=...) and sync=True, a failed postcondition does not raise.
  The call returns the agent result with {"verification": {"mode": "client",
  "status": "pass" | "fail"}} attached (merged into a dict result, else the result is
  wrapped as {"result": ..., "verification": ...}).
- Verification ERRORS (asyncpg connection, query or identifier validation) propagate as
  exceptions on the sync path. "unknown" is only set on the background (sync=False)
  path and never appears in a returned result. The Literal below keeps it for
  completeness, and `journal_verification_errors` covers the exception case.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Literal, NotRequired, TypedDict

from smartmemory import SmartMemory

logger = logging.getLogger(__name__)

ERROR_PREFIX = "synathic.verification."
ERROR_CLASSIFICATION = f"{ERROR_PREFIX}error"


class VerificationEnvelope(TypedDict):
    mode: str
    status: Literal["pass", "fail", "unknown"]


class _PostconditionRequired(TypedDict):
    postcondition: str
    table: str
    match_field: str


class Postcondition(_PostconditionRequired, total=False):
    """The @expect kwargs, passed by the caller (Synathic does not return them)."""

    expected_field: str
    expected_value: Any
    timestamp_column: NotRequired[str]


def classification_for(status: str) -> str:
    """Synathic has no failure classes beyond status, so error_type = prefix + status."""
    return f"{ERROR_PREFIX}{status}"


def _envelope(result: Any) -> VerificationEnvelope:
    if not isinstance(result, dict):
        raise ValueError(f"expected a dict carrying a 'verification' key, got {type(result).__name__}")
    verification = result.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("result has no 'verification' dict: the function was not verified (plain def, or @expect not applied)")
    if verification.get("status") not in ("pass", "fail", "unknown"):
        raise ValueError(f"unknown verification status {verification.get('status')!r}")
    return verification  # type: ignore[return-value]


def _check_postcondition(postcondition: Postcondition) -> None:
    if not isinstance(postcondition, dict):
        raise ValueError(f"postcondition must be a dict of @expect kwargs, got {type(postcondition).__name__}")
    missing = [k for k in ("postcondition", "table", "match_field") if not postcondition.get(k)]
    if missing:
        raise ValueError(f"postcondition is missing required keys: {missing}")


def record_verification(
    sm: SmartMemory, result: Any, *, action: str, postcondition: Postcondition, args: dict[str, Any] | None = None
) -> str | None:
    """Journal a non-passing Synathic verification. Returns the item id, or None on an explicit "pass".

    A result without a verification envelope, or with an unknown status, raises ValueError.

    result.verification.status -> error_type (via classification_for)
    result minus "verification" -> content (what the agent returned, json)
    postcondition (@expect kwargs: name, table, match_field, expected_*)
                               -> content (only what Synathic checked, no claim about what it found)
    action + args              -> context (the text check_before_retry searches on)
    """
    verification = _envelope(result)
    status = verification["status"]
    if status == "pass":
        return None
    _check_postcondition(postcondition)
    args = args or {}
    match_field = postcondition["match_field"]
    if match_field not in args:
        raise ValueError(f"args is missing the match field {match_field!r}: Synathic rejects a None match value, so the journal needs the real one")
    body = {k: v for k, v in result.items() if k != "verification"}
    checked = " ".join(
        f"{k}={postcondition[k]!r}" for k in ("postcondition", "table", "match_field", "timestamp_column", "expected_field", "expected_value") if k in postcondition
    )
    try:
        rendered = json.dumps(body, default=str)
    except TypeError:
        rendered = f"{body!r} (repr, not JSON)"
    content = (
        f"Synathic ({verification.get('mode')}) verification status={status} for {action}: {checked} "
        f"value={args[match_field]!r}. Agent returned: {rendered}"
    )
    return sm.log_failure(
        error_type=classification_for(status), content=content, context=f"{action} {args}",
        agent_id="synathic", domain="database-write",
    )


@contextmanager
def journal_verification_errors(sm: SmartMemory, *, action: str, args: dict[str, Any] | None = None) -> Iterator[None]:
    """Journal any exception raised by a decorated call as `synathic.verification.error`, then re-raise.

    Covers verification errors (bad DSN, unreachable Postgres, invalid identifier). It also sees the
    agent function's own exceptions, since both surface from the same call.
    """
    try:
        yield
    except Exception as exc:
        try:
            sm.log_failure(
                error_type=ERROR_CLASSIFICATION,
                content=f"Synathic-decorated call for {action} raised {type(exc).__name__}: {exc}",
                context=f"{action} {args or {}}",
                agent_id="synathic",
                domain="database-write",
            )
        except Exception:
            logger.warning("could not journal %s for %s", type(exc).__name__, action, exc_info=True)
        raise


def check_before_retry(sm: SmartMemory, *, classification: str, action: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Return past journaled failures for this error_type whose context resembles `action`."""
    return sm.check_failure_before_retry(error_type=classification, context=action, top_k=top_k)
