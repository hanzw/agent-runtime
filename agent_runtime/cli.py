from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory import ReMeMemory
from .policy import PolicyRuntime


_MAX_EVENT_BYTES = 1024 * 1024


def run(event: str, raw: str, home: Path | str | None = None) -> int:
    root = Path(home).expanduser().resolve() if home else Path.home()
    state = root / ".agent-runtime" / "state"
    runtime = PolicyRuntime(state)

    try:
        payload = _payload(raw)
        if event in {"PreToolUse", "PermissionRequest"}:
            result = runtime.handle_raw(event, raw)
            if result.blocked:
                if event == "PermissionRequest":
                    print(
                        json.dumps(
                            {
                                "hookSpecificOutput": {
                                    "hookEventName": "PermissionRequest",
                                    "decision": {"behavior": "deny", "message": result.reason},
                                }
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
                print(result.reason, file=sys.stderr)
                return 2
            print("{}")
            return 0

        runtime.handle(event, payload)
        if event == "SessionStart":
            _write_context(event, _session_start(root))
        elif event == "SubagentStart":
            _write_context(event, _subagent_start())
        elif event == "UserPromptSubmit":
            _write_context(event, _prompt_context(root, payload))
        elif event == "Maintenance":
            _maintenance(root, state)
            print("{}")
        else:
            print("{}")
        return 0
    except Exception as error:
        category = type(error).__name__
        if event in {"PreToolUse", "PermissionRequest"}:
            print(f"Global policy runtime failed closed ({category}).", file=sys.stderr)
            if event == "PermissionRequest":
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PermissionRequest",
                                "decision": {
                                    "behavior": "deny",
                                    "message": f"Global policy runtime failed closed ({category}).",
                                },
                            }
                        },
                        sort_keys=True,
                    )
                )
                return 0
            return 2
        print(f"Global hook observer failed open ({category}).", file=sys.stderr)
        print("{}")
        return 0


def _payload(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Hook payload must be a JSON object.")
    return data


def _session_start(home: Path) -> str:
    reme_status = "healthy" if _ensure_reme(home) else "unavailable"
    return (
        "Global policy runtime is active. Native Codex/Claude Skill discovery is authoritative; "
        f"no shadow capability registry is installed. ReMe: {reme_status}."
    )


def _prompt_context(home: Path, payload: dict[str, Any]) -> str:
    prompt = str(payload.get("prompt", payload.get("user_prompt", "")))
    return ReMeMemory(home).context(prompt, _cwd(payload))


def _subagent_start() -> str:
    return (
        "The global side-effect policy applies to this subagent. "
        "Use native project, global, and plugin Skills when relevant."
    )


def _maintenance(home: Path, state: Path) -> None:
    logs = (
        home / "Library" / "Logs" / "reme" / "service.log",
        home / "Library" / "Logs" / "reme" / "service-error.log",
    )
    for log in logs:
        if log.is_file() and not log.is_symlink():
            os.chmod(log, 0o600)

    reme_ok = _ensure_reme(home)
    now = datetime.now(timezone.utc).isoformat()
    _atomic_json(
        state / "heartbeat.json",
        {
            "last_run": now,
            "last_success": now if reme_ok else None,
            "reme_status": "healthy" if reme_ok else "unavailable",
            "status": "ok" if reme_ok else "degraded",
        },
    )


def _ensure_reme(home: Path) -> bool:
    return ReMeMemory(home).healthy(timeout=8)


def _cwd(payload: dict[str, Any]) -> Path:
    value = str(payload.get("cwd", "")).strip()
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def _write_context(event: str, context: str) -> None:
    if not context:
        print("{}")
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": context,
                }
            },
            sort_keys=True,
        )
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Global Codex and Claude hook dispatcher.")
    parser.add_argument("--event", required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(_MAX_EVENT_BYTES + 1)
    if len(raw) > _MAX_EVENT_BYTES:
        if args.event in {"PreToolUse", "PermissionRequest"}:
            print("Hook payload exceeded the 1 MiB policy limit.", file=sys.stderr)
            return 2
        print("{}")
        return 0
    return run(args.event, raw.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    raise SystemExit(main())
