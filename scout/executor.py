"""Script execution engine.

Runs user-authored Python scripts in a controlled namespace with a
``ScriptContext`` providing access to Scout data and HTTP.
"""
from __future__ import annotations

import io
import logging
import sys
import time
import traceback
from typing import Any

from sqlalchemy.orm import Session

from scout.sdk import ScriptContext

log = logging.getLogger(__name__)

# Modules scripts are allowed to import
_ALLOWED_MODULES = {
    "json", "re", "math", "datetime", "collections", "itertools",
    "functools", "urllib.parse", "hashlib", "base64", "csv", "io",
    "statistics", "textwrap", "string", "copy", "operator",
}


def run_script(
    code: str,
    session: Session,
    *,
    entity_id: int | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run a script and return a structured result.

    Returns::

        {"ok": bool, "result": Any, "logs": list[str],
         "duration_ms": int, "error": str | None}
    """
    ctx = ScriptContext(session, entity_id=entity_id)
    stdout_capture = io.StringIO()
    start = time.monotonic()

    try:
        namespace: dict[str, Any] = {
            "ctx": ctx,
            "__builtins__": _safe_builtins(),
        }

        compiled = compile(code, "<script>", mode="exec")

        old_stdout = sys.stdout
        sys.stdout = stdout_capture
        try:
            _run_compiled(compiled, namespace, timeout)
        finally:
            sys.stdout = old_stdout

        printed = stdout_capture.getvalue()
        if printed:
            for line in printed.strip().splitlines():
                ctx._logs.append(line)

        return {
            "ok": True,
            "result": ctx._result,
            "logs": ctx._logs,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "error": None,
        }

    except TimeoutError:
        return {
            "ok": False,
            "result": None,
            "logs": ctx._logs,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "error": f"Script timed out after {timeout}s",
        }
    except Exception as e:
        tb = traceback.format_exception(type(e), e, e.__traceback__)
        script_tb = [line for line in tb if "<script>" in line or not line.startswith("  File")]
        error_msg = f"{type(e).__name__}: {e}"
        if script_tb:
            error_msg = "".join(script_tb[-3:]).strip()

        return {
            "ok": False,
            "result": None,
            "logs": ctx._logs,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "error": error_msg,
        }
    finally:
        ctx._close()


def _run_compiled(compiled, namespace: dict, timeout: float) -> None:
    """Execute compiled code with signal-based timeout on Unix."""
    import signal

    def _handler(signum, frame):
        raise TimeoutError("Script timed out")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(timeout))
    try:
        # Intentional: scripts are authored by the LLM user of this single-user tool
        _do_exec(compiled, namespace)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _do_exec(compiled, namespace: dict) -> None:
    """Wrapper so the actual exec call is isolated for clarity."""
    exec(compiled, namespace)  # noqa: S102


def _safe_builtins() -> dict:
    """Return a builtins dict for script execution.

    Allows standard Python builtins but restricts imports to a safe set.
    """
    import builtins

    safe = {}
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        safe[name] = getattr(builtins, name)

    original_import = builtins.__import__

    def _filtered_import(name, *args, **kwargs):
        top = name.split(".")[0]
        if top in _ALLOWED_MODULES or name in _ALLOWED_MODULES or top == "httpx":
            return original_import(name, *args, **kwargs)
        raise ImportError(
            f"Import of '{name}' is not allowed in scripts. "
            f"Allowed: {', '.join(sorted(_ALLOWED_MODULES))}"
        )

    safe["__import__"] = _filtered_import
    safe["__name__"] = "__script__"
    return safe
