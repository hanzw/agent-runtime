from __future__ import annotations

from pathlib import Path


def canonical_repository_root(path: Path | str) -> Path:
    """Return the main repository root for a checkout or Git worktree."""
    current = Path(path).expanduser().resolve()
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate
        if not marker.is_file():
            continue
        try:
            prefix, value = marker.read_text(encoding="utf-8").strip().split(":", 1)
        except (OSError, ValueError):
            return candidate
        if prefix.strip().lower() != "gitdir":
            return candidate
        git_dir = Path(value.strip())
        if not git_dir.is_absolute():
            git_dir = candidate / git_dir
        git_dir = git_dir.resolve()
        dot_git = next(
            (parent for parent in (git_dir, *git_dir.parents) if parent.name == ".git"),
            None,
        )
        if dot_git:
            relative = git_dir.relative_to(dot_git)
            if len(relative.parts) == 2 and relative.parts[0] == "worktrees":
                return dot_git.parent
        return candidate
    return current
