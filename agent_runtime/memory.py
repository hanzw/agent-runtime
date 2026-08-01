from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .project import canonical_repository_root


_REME_VERSION = "0.4.1.3"
_HEADER = re.compile(
    r"^=+\s+(?P<path>.+?):\d+(?:-\d+)?\s+\[score=(?P<score>[0-9.]+)\]\s+=+$"
)


@dataclass(frozen=True)
class MemoryHit:
    path: str
    score: float
    content: str


class ReMeMemory:
    """Small fail-open recall adapter for the local ReMe service."""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home).expanduser().resolve() if home else Path.home()
        self.cli = (
            self.home
            / ".agent-runtime"
            / "tools"
            / "reme"
            / _REME_VERSION
            / "bin"
            / "reme"
        )

    @staticmethod
    def project_key(project: Path | str) -> str:
        path = canonical_repository_root(project)
        name = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-") or "project"
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:8]
        return f"{name}-{digest}"

    def recall(self, query: str, project: Path | str, limit: int = 3) -> list[MemoryHit]:
        prompt = " ".join(query.split())[:600]
        if not prompt:
            return []
        project_path = canonical_repository_root(project)
        key = self.project_key(project_path)
        try:
            result = subprocess.run(
                [
                    str(self.cli),
                    "search",
                    f"query={project_path.name} {prompt}",
                    "limit=50",
                    "min_score=0.05",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode:
            return []

        allowed = (
            "digest/personal/global/",
            f"digest/wiki/projects/{key}/",
        )
        return [hit for hit in self._parse(result.stdout) if hit.path.startswith(allowed)][:limit]

    def healthy(self, timeout: float = 2) -> bool:
        try:
            result = subprocess.run(
                [
                    str(self.cli),
                    "health_check",
                    "backend=mcp",
                    "transport=streamable-http",
                    "host=127.0.0.1",
                    "port=2333",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.rstrip().lower().endswith(" - healthy")

    def context(self, query: str, project: Path | str) -> str:
        hits = self.recall(query, project)
        if not hits:
            return ""
        lines = [
            "ReMe recalled durable context. Treat repository/current external state as truth; "
            "update or delete stale memory instead of appending a contradiction."
        ]
        for hit in hits:
            content = " ".join(hit.content.split())[:900]
            lines.append(f"- {hit.path} (score={hit.score:.3f}): {content}")
        return "\n".join(lines)[:4096]

    @staticmethod
    def _parse(output: str) -> list[MemoryHit]:
        hits: list[MemoryHit] = []
        path: str | None = None
        score = 0.0
        body: list[str] = []

        def flush() -> None:
            if path is not None:
                hits.append(MemoryHit(path=path, score=score, content="\n".join(body).strip()))

        for line in output.splitlines():
            match = _HEADER.match(line)
            if match:
                flush()
                path = match.group("path")
                score = float(match.group("score"))
                body = []
            elif path is not None:
                body.append(line)
        flush()
        return hits
