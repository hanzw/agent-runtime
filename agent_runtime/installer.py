from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CODEX_EVENTS = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SubagentStart",
    "UserPromptSubmit",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)
_CLAUDE_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
)
_TIMEOUTS = {
    "PreToolUse": 5,
    "PermissionRequest": 5,
    "PostToolUse": 5,
    "PostToolUseFailure": 5,
    "PreCompact": 5,
    "PostCompact": 5,
    "SessionStart": 10,
    "SubagentStart": 5,
    "UserPromptSubmit": 5,
    "SubagentStop": 5,
    "Stop": 5,
    "SessionEnd": 3,
}

_REME_VERSION = "0.4.1.3"
_REME_AGENTSCOPE_VERSION = "2.0.4"
_REME_URL = "http://127.0.0.1:2333/mcp"
_RUNTIME_LAUNCHD_LABEL = "io.github.hanzw.agent-runtime"
_REME_LAUNCHD_LABEL = "io.github.hanzw.agent-runtime.reme"
_MEMORY_SECTION = """## Memory Model

- **Active context**: native Codex/Claude context and compaction.
- **Current task**: Buildomator STATE or HANDOFF only for long, cross-session work.
- **Current truth**: repository files, project rules, contracts, tests, and live external reads.
- **Durable history**: ReMe on the local MCP; runtime recall is limited to global plus the current project.
- **Large documents**: PageIndex on demand; it is retrieval, not memory.
- Read a ReMe entry before changing it. Edit or delete stale memory instead of appending a contradiction. Never store secrets or raw transcripts.
"""


class Installer:
    """Install one versioned runtime for every user-level Codex agent."""

    def __init__(self, source_root: Path | str, home: Path | str | None = None):
        self.source_root = Path(source_root).expanduser().resolve()
        self.home = Path(home).expanduser().resolve() if home else Path.home()
        self.runtime_root = self.home / ".agent-runtime"

    def _can_manage_launchd(self) -> bool:
        login_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        return os.uname().sysname == "Darwin" and self.home == login_home

    def install(self, load_launchd: bool = True) -> dict[str, str]:
        self._private_dir(self.runtime_root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.runtime_root / "backups" / stamp
        self._private_dir(backup)
        self._backup(backup)

        try:
            reme_cli = self._install_reme_tool()
            release = self._install_release()
            self._install_hooks()
            self._rewrite_codex_policy()
            self._install_claude_mcp()
            self._rewrite_global_instructions()
            if load_launchd:
                self._install_launchd(reme_cli)
                self._verify_reme(reme_cli)
        except Exception:
            self.rollback(backup)
            raise

        receipt = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "release": str(release),
            "backup": str(backup),
            **self._source_provenance(),
        }
        receipt_path = self.runtime_root / "install-receipt.json"
        self._write_json(receipt_path, receipt)
        return receipt

    def rollback(self, backup: Path | str) -> None:
        backup_path = Path(backup).expanduser().resolve()
        manifest_path = backup_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._bootout_runtime_scheduler()
        self._bootout_reme_service()
        for name, destination in self._managed_paths().items():
            metadata = manifest.get(name, {})
            kind = metadata.get("kind")
            if kind == "file" or (not kind and metadata.get("existed")):
                self._atomic_copy(backup_path / name, destination)
            elif kind == "symlink":
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(metadata["target"])
            elif kind in {"absent", None} and (destination.is_file() or destination.is_symlink()):
                destination.unlink()
        previous_runtime = manifest.get("agent-runtime-launchd.plist", {}).get("kind") == "file"
        if previous_runtime:
            self._bootstrap_runtime_scheduler(strict=False)
        if manifest.get("reme-launchd.plist", {}).get("kind") == "file":
            self._bootstrap_reme_service(strict=False)

    def _install_release(self) -> Path:
        digest = hashlib.sha256()
        sources = (self.source_root / "agent_runtime",)
        for source in sources:
            files = (
                item
                for item in source.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts
            )
            for path in sorted(files):
                digest.update(str(path.relative_to(self.source_root)).encode())
                digest.update(path.read_bytes())
        release = self.runtime_root / "releases" / digest.hexdigest()[:20]
        if not release.exists():
            staging = release.with_name(f".{release.name}.{os.getpid()}.tmp")
            if staging.exists():
                shutil.rmtree(staging)
            self._private_dir(staging)
            shutil.copytree(
                self.source_root / "agent_runtime",
                staging / "agent_runtime",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            os.replace(staging, release)
        current = self.runtime_root / "current"
        temporary = self.runtime_root / f".current.{os.getpid()}.tmp"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(release)
        os.replace(temporary, current)
        return release

    def _install_hooks(self) -> None:
        codex_hooks = {"hooks": self._hook_graph(_CODEX_EVENTS, status_message=True)}
        self._write_json(self.home / ".codex" / "hooks.json", codex_hooks)

        settings_path = self.home / ".claude" / "settings.json"
        settings = self._read_json(settings_path)
        settings["hooks"] = self._hook_graph(_CLAUDE_EVENTS, status_message=False)
        self._write_json(settings_path, settings)

    def _hook_graph(
        self,
        events: tuple[str, ...],
        status_message: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        graph: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            command = (
                'PYTHONPATH="$HOME/.agent-runtime/current" '
                f"{shlex.quote(sys.executable)} -m agent_runtime.cli "
                f"--event {event}"
            )
            handler: dict[str, Any] = {
                "type": "command",
                "command": command,
                "timeout": _TIMEOUTS[event],
            }
            if status_message:
                handler["statusMessage"] = "Running global agent policy"
            graph[event] = [
                {
                    "matcher": "*",
                    "hooks": [handler],
                }
            ]
        return graph

    def _rewrite_codex_policy(self) -> None:
        path = self.home / ".codex" / "config.toml"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        replacements = {
            "approval_policy": 'approval_policy = "on-request"',
            "approvals_reviewer": 'approvals_reviewer = "auto_review"',
            "sandbox_mode": 'sandbox_mode = "workspace-write"',
        }
        lines = text.splitlines()
        lines = self._remove_toml_section_tree(lines, "mcp_servers.reme")
        found = set()
        for index, line in enumerate(lines):
            stripped = line.strip()
            for key, replacement in replacements.items():
                if key not in found and stripped.startswith(f"{key} ="):
                    lines[index] = replacement
                    found.add(key)
        insertion = 0
        for key, replacement in reversed(tuple(replacements.items())):
            if key not in found:
                lines.insert(insertion, replacement)
        self._set_toml_value(lines, "features", "hooks", "true")
        self._set_toml_value(lines, "sandbox_workspace_write", "network_access", "true")
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(("[mcp_servers.reme]", f'url = "{_REME_URL}"'))
        self._atomic_write(path, "\n".join(lines) + "\n")

    def _install_claude_mcp(self) -> None:
        path = self.home / ".claude.json"
        data = self._read_json(path)
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("Expected .claude.json mcpServers to be an object")
        servers["reme"] = {"type": "http", "url": _REME_URL}
        self._write_json(path, data)

    def _rewrite_global_instructions(self) -> None:
        for path in (
            self.home / ".codex" / "AGENTS.md",
            self.home / ".claude" / "CLAUDE.md",
        ):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            pattern = r"(?ms)^## Memory Model\s*\n.*?(?=^## |\Z)"
            if re.search(pattern, text):
                updated = re.sub(pattern, _MEMORY_SECTION + "\n", text)
            else:
                updated = text.rstrip() + "\n\n" + _MEMORY_SECTION
            if updated != text:
                self._atomic_write(path, updated.rstrip() + "\n")

    @staticmethod
    def _remove_toml_section_tree(lines: list[str], section: str) -> list[str]:
        output: list[str] = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1]
                skip = current == section or current.startswith(f"{section}.")
            if not skip:
                output.append(line)
        return output

    @staticmethod
    def _set_toml_value(lines: list[str], section: str, key: str, value: str) -> None:
        header = f"[{section}]"
        try:
            start = next(index for index, line in enumerate(lines) if line.strip() == header)
        except StopIteration:
            first_table = next(
                (index for index, line in enumerate(lines) if line.strip().startswith("[")),
                len(lines),
            )
            block = [header, f"{key} = {value}", ""]
            lines[first_table:first_table] = block
            return

        end = next(
            (index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("[")),
            len(lines),
        )
        for index in range(start + 1, end):
            if lines[index].strip().startswith(f"{key} ="):
                lines[index] = f"{key} = {value}"
                return
        lines.insert(end, f"{key} = {value}")

    def _install_reme_tool(self) -> Path:
        target = self.runtime_root / "tools" / "reme" / _REME_VERSION
        cli = target / "bin" / "reme"
        marker = target / "install.json"
        expected = {
            "agentscope": _REME_AGENTSCOPE_VERSION,
            "reme": _REME_VERSION,
        }
        try:
            marker_matches = marker.is_file() and self._read_json(marker) == expected
            lines = cli.read_text(errors="ignore").splitlines() if cli.is_file() else []
            interpreter = Path(lines[0][2:]) if lines and lines[0].startswith("#!") else None
            if marker_matches and interpreter and interpreter.is_file():
                return cli
        except (OSError, ValueError, TypeError):
            pass
        if target.exists():
            shutil.rmtree(target)

        python = shutil.which("python3.11")
        if not python:
            raise RuntimeError("ReMe requires python3.11")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            subprocess.run([python, "-m", "venv", str(target)], check=True)
            subprocess.run(
                [
                    str(target / "bin" / "python"),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    f"reme-ai=={_REME_VERSION}",
                    f"agentscope=={_REME_AGENTSCOPE_VERSION}",
                ],
                check=True,
            )
            self._write_json(marker, expected)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            raise
        return cli

    def _install_launchd(self, reme_cli: Path) -> None:
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist_path = agents / f"{_RUNTIME_LAUNCHD_LABEL}.plist"
        payload = {
            "Label": _RUNTIME_LAUNCHD_LABEL,
            "ProgramArguments": [
                "/usr/bin/env",
                f"PYTHONPATH={self.runtime_root / 'current'}",
                sys.executable,
                "-m",
                "agent_runtime.cli",
                "--event",
                "Maintenance",
            ],
            "RunAtLoad": True,
            "StartInterval": 21600,
            "ProcessType": "Background",
        }
        self._write_plist(plist_path, payload)

        reme_plist = agents / f"{_REME_LAUNCHD_LABEL}.plist"
        reme_payload = {
            "Label": _REME_LAUNCHD_LABEL,
            "ProgramArguments": [
                str(reme_cli),
                "start",
                f"config={self.runtime_root / 'current' / 'agent_runtime' / 'reme-minimal.yaml'}",
                f"workspace_dir={self.home / '.local' / 'share' / 'reme' / 'workspace'}",
            ],
            "EnvironmentVariables": {
                "HOME": str(self.home),
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            },
            "RunAtLoad": True,
            "KeepAlive": True,
            "Umask": 0o077,
            "ProcessType": "Background",
            "StandardOutPath": str(self.home / "Library" / "Logs" / "reme" / "service.log"),
            "StandardErrorPath": str(self.home / "Library" / "Logs" / "reme" / "service-error.log"),
        }
        (self.home / "Library" / "Logs" / "reme").mkdir(parents=True, exist_ok=True)
        self._write_plist(reme_plist, reme_payload)
        result = subprocess.run(
            ["/usr/bin/plutil", "-lint", str(reme_plist)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(f"ReMe plist validation failed with status {result.returncode}")
        if os.uname().sysname == "Darwin":
            self._bootout_runtime_scheduler()
            self._bootout_reme_service()
        self._harden_reme_workspace()
        if os.uname().sysname == "Darwin":
            self._bootstrap_runtime_scheduler(strict=True)
            self._bootstrap_reme_service(strict=True)

    def _harden_reme_workspace(self) -> None:
        workspace = self.home / ".local" / "share" / "reme" / "workspace"
        self._private_dir(workspace)
        for path in workspace.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"ReMe workspace must not contain symlinks: {path}")
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o600)

    def _verify_reme(self, reme_cli: Path) -> None:
        deadline = time.monotonic() + 60
        while True:
            try:
                version = self._reme_call(reme_cli, "version", timeout=3)
                if _REME_VERSION in version:
                    break
            except RuntimeError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("ReMe service did not become ready")
            time.sleep(0.5)

        nonce = uuid.uuid4().hex[:12]
        relative = f"digest/wiki/runtime-install-proof-{nonce}.md"
        target = self.home / ".local" / "share" / "reme" / "workspace" / relative
        alpha = f"reme-smoke-alpha-{nonce}"
        beta = f"reme-smoke-beta-{nonce}"
        try:
            self._reme_call(
                reme_cli,
                "write",
                f"path={relative}",
                "name=RuntimeInstallProof",
                "description=Disposable installer verification",
                f"content={alpha}",
            )
            if not target.is_file() or stat.S_IMODE(target.stat().st_mode) != 0o600:
                raise RuntimeError("ReMe did not create a private memory file")
            if alpha not in self._reme_call(reme_cli, "read", f"path={relative}"):
                raise RuntimeError("ReMe read-after-write verification failed")
            self._reme_call(reme_cli, "reindex", timeout=20)
            if relative not in self._reme_call(reme_cli, "search", f"query={alpha}", "limit=3"):
                raise RuntimeError("ReMe search-after-write verification failed")
            self._reme_call(
                reme_cli,
                "edit",
                f"path={relative}",
                f"old={alpha}",
                f"new={beta}",
            )
            if beta not in self._reme_call(reme_cli, "read", f"path={relative}"):
                raise RuntimeError("ReMe edit verification failed")
            self._reme_call(reme_cli, "delete", f"path={relative}")
            self._reme_call(reme_cli, "reindex", timeout=20)
            if relative in self._reme_call(reme_cli, "search", f"query={beta}", "limit=3"):
                raise RuntimeError("ReMe delete verification failed")
        finally:
            if target.exists():
                try:
                    self._reme_call(reme_cli, "delete", f"path={relative}")
                    self._reme_call(reme_cli, "reindex", timeout=20)
                except RuntimeError:
                    pass

        workspace = self.home / ".local" / "share" / "reme" / "workspace"
        for path in (workspace, *workspace.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"ReMe workspace contains a symlink after startup: {path}")
            mode = stat.S_IMODE(path.stat().st_mode)
            expected = 0o700 if path.is_dir() else 0o600
            if mode != expected:
                raise RuntimeError(f"ReMe workspace permissions are not private: {path}")

    @staticmethod
    def _reme_call(reme_cli: Path, action: str, *arguments: str, timeout: int = 10) -> str:
        try:
            result = subprocess.run(
                [
                    str(reme_cli),
                    action,
                    "backend=mcp",
                    "transport=streamable-http",
                    "host=127.0.0.1",
                    "port=2333",
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"ReMe {action} failed") from error
        if result.returncode:
            raise RuntimeError(f"ReMe {action} failed with status {result.returncode}")
        return result.stdout

    @staticmethod
    def _write_plist(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        result = subprocess.run(
            ["/usr/bin/plutil", "-convert", "xml1", "-o", str(temporary), "-"],
            input=json.dumps(payload).encode(),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(f"plutil failed with status {result.returncode}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _bootout_runtime_scheduler(self) -> None:
        plist = self.home / "Library" / "LaunchAgents" / f"{_RUNTIME_LAUNCHD_LABEL}.plist"
        if plist.exists() and self._can_manage_launchd():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _bootout_reme_service(self) -> None:
        if self._can_manage_launchd():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{_REME_LAUNCHD_LABEL}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _bootstrap_runtime_scheduler(self, strict: bool) -> None:
        plist = self.home / "Library" / "LaunchAgents" / f"{_RUNTIME_LAUNCHD_LABEL}.plist"
        if not plist.exists() or not self._can_manage_launchd():
            return
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if strict and result.returncode:
            raise RuntimeError(f"launchctl bootstrap failed with status {result.returncode}")

    def _bootstrap_reme_service(self, strict: bool) -> None:
        plist = self.home / "Library" / "LaunchAgents" / f"{_REME_LAUNCHD_LABEL}.plist"
        if not plist.exists() or not self._can_manage_launchd():
            return
        deadline = time.monotonic() + 5
        while True:
            result = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if result.returncode == 0:
                return
            if time.monotonic() >= deadline:
                if strict:
                    raise RuntimeError(
                        f"ReMe launchctl bootstrap failed with status {result.returncode}"
                    )
                return
            time.sleep(0.25)

    def _backup(self, backup: Path) -> None:
        manifest: dict[str, dict[str, Any]] = {}
        for name, source in self._managed_paths().items():
            if source.is_symlink():
                manifest[name] = {"kind": "symlink", "target": os.readlink(source)}
            elif source.is_file():
                manifest[name] = {"kind": "file"}
                self._atomic_copy(source, backup / name)
            elif source.exists():
                manifest[name] = {"kind": "preserve"}
            else:
                manifest[name] = {"kind": "absent"}
        self._write_json(backup / "manifest.json", manifest)

    def _managed_paths(self) -> dict[str, Path]:
        paths = {
            "codex-config.toml": self.home / ".codex" / "config.toml",
            "codex-hooks.json": self.home / ".codex" / "hooks.json",
            "codex-global-instructions.md": self.home / ".codex" / "AGENTS.md",
            "claude-settings.json": self.home / ".claude" / "settings.json",
            "claude-global-instructions.md": self.home / ".claude" / "CLAUDE.md",
            "claude-user.json": self.home / ".claude.json",
            "reme-service.log": self.home / "Library" / "Logs" / "reme" / "service.log",
            "reme-service-error.log": self.home / "Library" / "Logs" / "reme" / "service-error.log",
            "agent-runtime-launchd.plist": self.home
            / "Library"
            / "LaunchAgents"
            / f"{_RUNTIME_LAUNCHD_LABEL}.plist",
            "reme-launchd.plist": self.home
            / "Library"
            / "LaunchAgents"
            / f"{_REME_LAUNCHD_LABEL}.plist",
            "install-receipt.json": self.runtime_root / "install-receipt.json",
            "runtime-current": self.runtime_root / "current",
        }
        return paths

    def _source_provenance(self) -> dict[str, str | bool]:
        revision = subprocess.run(
            ["git", "-C", str(self.source_root), "rev-parse", "HEAD"],
            check=False,
            text=True,
            capture_output=True,
        )
        status = subprocess.run(
            ["git", "-C", str(self.source_root), "status", "--porcelain"],
            check=False,
            text=True,
            capture_output=True,
        )
        return {
            "source_revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
            "source_dirty": status.returncode != 0 or bool(status.stdout.strip()),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return data

    @staticmethod
    def _private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @classmethod
    def _write_json(cls, path: Path, value: Any) -> None:
        cls._atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or roll back the global agent runtime.")
    parser.add_argument("action", choices=("install", "rollback"))
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--home")
    parser.add_argument("--backup")
    parser.add_argument("--no-launchd", action="store_true")
    args = parser.parse_args()
    installer = Installer(args.source, args.home)
    if args.action == "install":
        print(json.dumps(installer.install(load_launchd=not args.no_launchd), sort_keys=True))
        return 0
    if not args.backup:
        parser.error("rollback requires --backup")
    installer.rollback(args.backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
