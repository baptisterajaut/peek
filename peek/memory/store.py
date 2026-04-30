"""Memory store: MEMORY.md index + typed .md entries with frontmatter.

Layout under `root/`:

    MEMORY.md             # index, one line per entry, no frontmatter
    user_baptiste.md      # entry, frontmatter + body
    feedback_no_glaze.md
    ...

Each entry file:

    ---
    name: Short title
    description: One-line hook used in MEMORY.md and for retrieval relevance
    type: user|feedback|project|reference
    ---

    Body markdown.

The index line for a given entry:

    - [Short title](feedback_no_glaze.md) — One-line hook
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

VALID_TYPES = {"user", "feedback", "project", "reference"}
INDEX_FILENAME = "MEMORY.md"
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?\n)---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)
_INDEX_LINE_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<filename>[^)]+)\)\s*—\s*(?P<hook>.+)$",
)


@dataclass
class Entry:
    name: str
    description: str
    type: str
    body: str
    filename: str  # basename, e.g. "feedback_no_glaze.md"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text or "entry"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group("fm").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm, m.group("body").lstrip("\n")


def _format_entry(name: str, description: str, type_: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {type_}\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _format_index_line(name: str, filename: str, hook: str) -> str:
    return f"- [{name}]({filename}) — {hook}"


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_resolved = self.root.resolve()
        self.index_path = self.root / INDEX_FILENAME

    def _safe_path(self, filename: str) -> Path | None:
        """Resolve a user/LLM-supplied filename, refusing path traversal.

        The model controls `filename` via read_memory_file / forget_memory
        tools; without this check, a hallucinated or hostile arg like
        '../config.conf' or '../../.ssh/id_rsa' would escape the memory dir.
        """
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            return None
        candidate = (self.root / filename).resolve()
        try:
            candidate.relative_to(self._root_resolved)
        except ValueError:
            return None
        return candidate

    def _filename_for(self, name: str, type_: str) -> str:
        base = f"{type_}_{slugify(name)}.md"
        if not (self.root / base).exists():
            return base
        # Disambiguate
        i = 2
        while True:
            candidate = f"{type_}_{slugify(name)}_{i}.md"
            if not (self.root / candidate).exists():
                return candidate
            i += 1

    def _read_index_lines(self) -> list[str]:
        if not self.index_path.exists():
            return []
        return self.index_path.read_text(encoding="utf-8").splitlines()

    def _write_index_lines(self, lines: list[str]) -> None:
        # Trim trailing empties, ensure single trailing newline.
        while lines and not lines[-1].strip():
            lines.pop()
        _atomic_write(self.index_path, "\n".join(lines) + "\n" if lines else "")

    def list_entries(self) -> list[Entry]:
        out: list[Entry] = []
        for path in sorted(self.root.glob("*.md")):
            if path.name == INDEX_FILENAME:
                continue
            fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if fm.get("type") not in VALID_TYPES:
                continue
            out.append(Entry(
                name=fm.get("name", path.stem),
                description=fm.get("description", ""),
                type=fm.get("type", "user"),
                body=body,
                filename=path.name,
            ))
        return out

    def read_entry(self, filename: str) -> Entry | None:
        path = self._safe_path(filename)
        if path is None or not path.exists() or path.name == INDEX_FILENAME:
            return None
        fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm.get("type") not in VALID_TYPES:
            return None
        return Entry(
            name=fm.get("name", path.stem),
            description=fm.get("description", ""),
            type=fm.get("type", "user"),
            body=body,
            filename=path.name,
        )

    def write_entry(
        self,
        name: str,
        description: str,
        type_: str,
        body: str,
        filename: str | None = None,
    ) -> Entry:
        if type_ not in VALID_TYPES:
            raise ValueError(f"invalid type: {type_!r} (must be one of {VALID_TYPES})")
        # Strip newlines from frontmatter fields — a stray '\n---\n' inside
        # a name would forge a fresh frontmatter block on parse.
        name = name.replace("\n", " ").replace("\r", " ").strip()
        description = description.replace("\n", " ").replace("\r", " ").strip()
        if filename is None:
            filename = self._filename_for(name, type_)
        path = self._safe_path(filename)
        if path is None or path.name == INDEX_FILENAME:
            raise ValueError(f"unsafe or reserved filename: {filename!r}")
        _atomic_write(path, _format_entry(name, description, type_, body))
        self._upsert_index_line(name, path.name, description)
        return Entry(
            name=name, description=description, type=type_,
            body=body, filename=path.name,
        )

    def delete_entry(self, filename: str) -> bool:
        path = self._safe_path(filename)
        if path is None or not path.exists() or path.name == INDEX_FILENAME:
            return False
        path.unlink()
        self._remove_index_line(path.name)
        return True

    def _upsert_index_line(self, name: str, filename: str, hook: str) -> None:
        lines = self._read_index_lines()
        new_line = _format_index_line(name, filename, hook)
        for i, line in enumerate(lines):
            m = _INDEX_LINE_RE.match(line)
            if m and m.group("filename") == filename:
                lines[i] = new_line
                self._write_index_lines(lines)
                return
        lines.append(new_line)
        self._write_index_lines(lines)

    def _remove_index_line(self, filename: str) -> None:
        lines = self._read_index_lines()
        kept = []
        for line in lines:
            m = _INDEX_LINE_RE.match(line)
            if m and m.group("filename") == filename:
                continue
            kept.append(line)
        self._write_index_lines(kept)

    def assemble_for_prompt(self) -> str:
        """Return the index content for system-prompt injection.

        Empty string if no entries — caller decides whether to inject a header.
        """
        if not self.index_path.exists():
            return ""
        return self.index_path.read_text(encoding="utf-8").rstrip()
