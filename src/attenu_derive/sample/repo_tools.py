"""
Shared repository tools for the sampling harnesses (T22). One implementation of the read-only repo surface every
harness gives its agents — list / read / search over a checkout, plus a sandboxed write for the deliverable — so
the file I/O, the skip-list, the size caps and the path-escape guard live in ONE place and cannot drift between
frameworks. Each harness wraps these pure functions in its own tool decorator (ADK plain callables, CrewAI
`@tool` returning JSON strings, etc.); the LOGIC is here, tested once (tests/test_repo_tools.py).

Caps are deliberately tight (a fan-out task re-sends every tool result each step; large results are the cost
driver): 80 files listed, 120 lines read, 40 matches. Everything is read-only over the checkout; `write_file`
writes only into the run's artifacts dir, never the repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MAX_LIST = 80
MAX_LINES = 120
MAX_MATCHES = 40
MAX_FILE_BYTES = 400_000
SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv", "vendor", ".tox", ".mypy_cache"}


class RepoTools:
    """Read-only repo surface over `repo`, with a sandboxed write into `artifacts`. Pure Python; no framework."""

    def __init__(self, repo: Path, artifacts: Path):
        self.repo = Path(repo).resolve()
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def _inside(self, p: str) -> Path:
        q = (self.repo / p).resolve()
        if self.repo not in q.parents and q != self.repo:
            raise ValueError("path escapes the repository")
        return q

    def _skip(self, p: Path) -> bool:
        return any(part in SKIP_DIRS for part in p.relative_to(self.repo).parts)

    def list_files(self, pattern: str = "**/*") -> dict:
        """Files matching a glob relative to the repo root (e.g. "src/**/*.py"), capped at MAX_LIST."""
        out = []
        for p in sorted(self.repo.glob(pattern)):
            if p.is_file() and not self._skip(p):
                out.append(str(p.relative_to(self.repo)))
                if len(out) >= MAX_LIST:
                    break
        return {"files": out, "truncated": len(out) >= MAX_LIST}

    def read_file(self, path: str, offset: int = 0, limit: int = MAX_LINES) -> dict:
        """Up to `limit` lines from line `offset` (0-based) of a repo file."""
        try:
            q = self._inside(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not q.is_file():
            return {"error": "not a file"}
        if q.stat().st_size > MAX_FILE_BYTES:
            return {"error": "file too large; use search_files"}
        lines = q.read_text(errors="replace").splitlines()
        lim = max(1, min(int(limit or MAX_LINES), MAX_LINES))
        off = max(0, int(offset or 0))
        return {"path": path, "offset": off, "lines": lines[off: off + lim], "total_lines": len(lines)}

    def search_files(self, pattern: str, glob: str = "**/*") -> dict:
        """Regex search over repo files; up to MAX_MATCHES {path, line, text}."""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return {"error": f"bad regex: {exc}"}
        hits = []
        for p in sorted(self.repo.glob(glob)):
            if not p.is_file() or self._skip(p) or p.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append({"path": str(p.relative_to(self.repo)), "line": i, "text": line.strip()[:200]})
                        if len(hits) >= MAX_MATCHES:
                            return {"matches": hits, "truncated": True}
            except (OSError, UnicodeDecodeError):
                continue
        return {"matches": hits, "truncated": False}

    def write_file(self, path: str, content: str) -> dict:
        """Write the deliverable into the run's artifacts dir (never the repo). One file per task."""
        name = Path(path).name or "OUTPUT.md"
        (self.artifacts / name).write_text(content)
        return {"written": name, "bytes": len(content.encode())}

    # Framework-string variants (CrewAI @tool bodies return strings) --------------------------------------------
    def list_files_json(self, pattern: str = "**/*") -> str:
        return json.dumps(self.list_files(pattern))

    def read_file_json(self, path: str, offset: int = 0, limit: int = MAX_LINES) -> str:
        return json.dumps(self.read_file(path, offset, limit))

    def search_files_json(self, pattern: str, glob: str = "**/*") -> str:
        return json.dumps(self.search_files(pattern, glob))

    def write_file_json(self, path: str, content: str) -> str:
        return json.dumps(self.write_file(path, content))
