"""Per-session operator settings, including a judge's API key.

PENDING-JUDGE §3. A judge pastes a key to run their own cycle. That key:

  - lives in this process's memory ONLY — never written to .env, never to
    the database, never logged, never returned by any endpoint;
  - takes precedence over .env for the life of the server process;
  - disappears when the process stops.

Anything that reports state reports `key_set: true|false` and nothing else.
The key itself has exactly one destination: the anthropic SDK.
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()

# Deliberately module-level and process-scoped. Single-user local tool
# (SPEC-APP §0.5), so there is no multi-tenant key mixing to worry about,
# and nothing here survives a restart — which is the point.
_STATE: dict = {
    "operator": "",
    "api_key": None,     # never leaves this module except to the SDK
    "model": None,
    "effort": None,
}

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def set_session(*, operator: str | None = None, api_key: str | None = None,
                model: str | None = None, effort: str | None = None) -> dict:
    """Update the session. Passing None leaves a field alone; passing an
    empty string for api_key clears it (a judge revoking their key)."""
    with _LOCK:
        if operator is not None:
            _STATE["operator"] = operator.strip()[:80]
        if api_key is not None:
            key = api_key.strip()
            _STATE["api_key"] = key or None
        if model is not None:
            _STATE["model"] = model.strip() or None
        if effort is not None:
            e = effort.strip().lower()
            _STATE["effort"] = e if e in VALID_EFFORTS else None
    return public_state()


def api_key() -> str | None:
    """The session key, if one was set. runtime falls back to .env."""
    with _LOCK:
        return _STATE["api_key"]


def model() -> str | None:
    with _LOCK:
        return _STATE["model"]


def effort() -> str | None:
    with _LOCK:
        return _STATE["effort"]


def operator() -> str:
    with _LOCK:
        return _STATE["operator"]


def public_state() -> dict:
    """Everything safe to send to a client. Note what is absent: the key,
    and any prefix, suffix or length of it."""
    with _LOCK:
        return {
            "operator": _STATE["operator"],
            "key_set": bool(_STATE["api_key"]),
            "model": _STATE["model"],
            "effort": _STATE["effort"],
        }


def clear() -> dict:
    with _LOCK:
        _STATE.update(operator="", api_key=None, model=None, effort=None)
    return public_state()
