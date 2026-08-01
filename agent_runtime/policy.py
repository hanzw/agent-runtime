from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DESTRUCTIVE_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)rm\s+-(?:[^\s]*r[^\s]*f|[^\s]*f[^\s]*r)\s+(?:/|~|\$HOME)(?:\s|$)"),
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+reset\s+--hard\b"),
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+clean\b[^\n]*(?:-fd|-df|-f\s+-d|-d\s+-f)"),
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+checkout\s+--(?:\s|$)"),
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+restore\b(?![^\n]*--staged\b)"),
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+stash\s+clear\b"),
    re.compile(r"\bgit\s+push\b[^\n]*(?:--force(?:-with-lease)?|-f)\b[^\n]*(?:main|master)\b"),
    re.compile(r"\b(?:DROP\s+(?:TABLE|DATABASE)|TRUNCATE\s+TABLE)\b", re.IGNORECASE),
)

_STASH_DROP = re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+stash\s+drop\b(?P<arguments>[^\n;&|]*)"
)
_EXACT_STASH_REF = re.compile(r"['\"]?stash@\{\d+\}['\"]?")

_PRODUCTION_DEPLOY = (
    re.compile(r"(^|[;&|]\s*)npm\s+run\s+deploy(?:\s|$)"),
    re.compile(r"\bwrangler(?:@\S+)?\s+pages\s+deploy\b(?![^\n]*--branch\s+(?:staging|preview)\b)"),
)

_PRODUCTION_DB = (
    re.compile(r"\bwrangler(?:@\S+)?\s+d1\s+execute\s+\S+\b[^\n]*--remote\b"),
)

_VERIFICATION_BYPASS = (
    re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+(?:commit|push)\b[^\n]*--no-verify\b"),
)

_PROTECTED_BRANCH_WRITE = (
    re.compile(
        r"\bgit(?:\s+-C\s+\S+)?\s+push\b[^\n]*"
        r"(?:\s|:)(?:refs/heads/)?(?:main|master)(?:\s|$)"
    ),
)

_GLOBAL_CONFIG_PATH = re.compile(
    r"(?:~|\$HOME|/Users/[^/]+|/home/[^/]+)/\.(?:codex|claude)/"
    r"(?:config\.toml|hooks\.json|settings\.json)"
)
_READ_ONLY_GLOBAL_CONFIG_COMMAND = re.compile(
    r"^\s*(?:cat|head|tail|less|more|stat|ls|rg|grep|wc|shasum|sha256sum|cmp|diff|test)\b"
    r"|^\s*sed\s+-n\b"
)
_SHELL_MUTATION_OPERATOR = re.compile(r"[;&|><\n`]|\$\(")
_READ_ONLY_GLOBAL_CONFIG_TOOLS = {
    "Glob",
    "Grep",
    "Read",
    "find",
    "list_dir",
    "read_file",
    "rg",
    "view_file",
}
_LOCAL_IO_LOCK = threading.Lock()


@dataclass(frozen=True)
class HookResult:
    blocked: bool = False
    reason: str = ""
    context: str = ""


class PolicyRuntime:
    """Normalize hook events, classify side effects, and record minimal evidence."""

    def __init__(self, state_dir: Path | str, max_log_bytes: int = 5 * 1024 * 1024):
        self.state_dir = Path(state_dir).expanduser()
        self.max_log_bytes = max_log_bytes
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)

    def handle_raw(self, event: str, raw: str) -> HookResult:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            if event in {"PreToolUse", "PermissionRequest"}:
                return HookResult(True, f"Invalid {event} event; refusing an unclassified tool call.")
            return HookResult()
        if not isinstance(payload, dict):
            if event in {"PreToolUse", "PermissionRequest"}:
                return HookResult(True, f"Invalid {event} event; expected a JSON object.")
            return HookResult()
        return self.handle(event, payload)

    def handle(self, event: str, payload: dict[str, Any]) -> HookResult:
        effect = self.classify(payload)
        if event in {"PreToolUse", "PermissionRequest"}:
            self.record(event, payload, effect)
            if effect == "destructive":
                return HookResult(True, "Destructive command blocked by the global policy runtime.")
            if effect == "verification-bypass":
                return HookResult(
                    True,
                    "Verification bypass blocked; run the repository's evidence workflow.",
                )
            if effect == "protected-branch-write":
                return HookResult(
                    True,
                    "Direct protected branch write blocked; use the repository's "
                    "agent:ship or reviewed merge workflow.",
                )
            if effect == "global-config-mutation":
                return HookResult(
                    True,
                    "Direct global agent configuration edit blocked; update the "
                    "versioned source and run its installer.",
                )
            return HookResult()

        self.record(event, payload, effect)
        return HookResult()

    def classify(self, payload: dict[str, Any]) -> str:
        command = self._command(payload)
        serialized_input = self._serialized_input(payload)
        if command and self._is_destructive_command(command):
            return "destructive"
        if command and any(pattern.search(command) for pattern in _VERIFICATION_BYPASS):
            return "verification-bypass"
        if command and any(pattern.search(command) for pattern in _PROTECTED_BRANCH_WRITE):
            return "protected-branch-write"
        if self._is_global_config_mutation(payload, command, serialized_input):
            return "global-config-mutation"
        if command and any(pattern.search(command) for pattern in _PRODUCTION_DEPLOY):
            return "production-deploy"
        if command and any(pattern.search(command) for pattern in _PRODUCTION_DB):
            return "production-db"
        return "normal"

    @staticmethod
    def _is_destructive_command(command: str) -> bool:
        if any(pattern.search(command) for pattern in _DESTRUCTIVE_PATTERNS):
            return True
        stash_drop = _STASH_DROP.search(command)
        if not stash_drop:
            return False
        arguments = stash_drop.group("arguments").strip()
        return _EXACT_STASH_REF.fullmatch(arguments) is None

    @staticmethod
    def _is_global_config_mutation(
        payload: dict[str, Any], command: str, serialized_input: str
    ) -> bool:
        if command:
            if not _GLOBAL_CONFIG_PATH.search(serialized_input):
                return False
            return not (
                _READ_ONLY_GLOBAL_CONFIG_COMMAND.search(command)
                and not _SHELL_MUTATION_OPERATOR.search(command)
                and not re.search(r"\bsed\b[^\n]*\s-i(?:\s|$)", command)
            )
        tool = str(payload.get("tool_name", payload.get("tool", "")))
        if tool == "apply_patch":
            tool_input = payload.get("tool_input", payload.get("input", {}))
            patch_text = (
                str(tool_input.get("patch", ""))
                if isinstance(tool_input, dict)
                else str(tool_input)
            )
            targets = re.findall(
                r"(?m)^\*\*\* (?:Add|Delete|Update) File:\s*(.+?)\s*$",
                patch_text,
            )
            return any(_GLOBAL_CONFIG_PATH.search(target) for target in targets)
        if not _GLOBAL_CONFIG_PATH.search(serialized_input):
            return False
        return tool not in _READ_ONLY_GLOBAL_CONFIG_TOOLS

    def record(self, event: str, payload: dict[str, Any], effect: str | None = None) -> None:
        tool_input = payload.get("tool_input", payload.get("input", {}))
        tool_response = payload.get("tool_response", payload.get("tool_output", payload.get("output")))
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "tool": str(payload.get("tool_name", payload.get("tool", "unknown"))),
            "session": self._short_hash(str(payload.get("session_id", "unknown"))),
            "cwd": self._cwd_identity(str(payload.get("cwd", ""))),
            "effect": effect or self.classify(payload),
            "input_fingerprint": self._fingerprint(tool_input),
            "response_kind": type(tool_response).__name__ if tool_response is not None else "none",
        }
        self._append_event(entry)

    @staticmethod
    def _command(payload: dict[str, Any]) -> str:
        tool = str(payload.get("tool_name", payload.get("tool", "")))
        if tool not in {"Bash", "exec_command", "shell", "bash"}:
            return ""
        tool_input = payload.get("tool_input", payload.get("input", {}))
        if isinstance(tool_input, dict):
            command = tool_input.get("command", tool_input.get("cmd", ""))
            if isinstance(command, list):
                return " ".join(str(part) for part in command)
            return str(command)
        return str(tool_input)

    @staticmethod
    def _serialized_input(payload: dict[str, Any]) -> str:
        value = payload.get("tool_input", payload.get("input", {}))
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, default=str)

    def _fingerprint(self, value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).encode()
        return hmac.new(self._fingerprint_key(), raw, hashlib.sha256).hexdigest()[:24]

    def _fingerprint_key(self) -> bytes:
        path = self.state_dir / "fingerprint.key"
        lock_path = self.state_dir / ".fingerprint.lock"
        with _LOCAL_IO_LOCK:
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if not path.exists():
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        os.write(fd, secrets.token_bytes(32))
                    finally:
                        os.close(fd)
                os.chmod(path, 0o600)
                value = path.read_bytes()
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        if len(value) != 32:
            raise RuntimeError("Invalid policy fingerprint key.")
        return value

    @staticmethod
    def _short_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    @staticmethod
    def _cwd_identity(value: str) -> str:
        path = Path(value).expanduser() if value else None
        return path.name if path else "unknown"

    def _append_event(self, entry: dict[str, Any]) -> None:
        path = self.state_dir / "events.jsonl"
        lock_path = self.state_dir / ".events.lock"
        with _LOCAL_IO_LOCK:
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if path.exists() and path.stat().st_size >= self.max_log_bytes:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    archive = self.state_dir / f"events.{stamp}.jsonl"
                    os.replace(path, archive)
                    os.chmod(archive, 0o600)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, (json.dumps(entry, sort_keys=True) + "\n").encode())
                finally:
                    os.close(fd)
                os.chmod(path, 0o600)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
