"""Personality loader — bundled defaults copied to user dir on first use."""

from __future__ import annotations

import shutil
from pathlib import Path

BUNDLED_DIR = Path(__file__).parent / "personalities"


def ensure_personalities(user_dir: Path) -> None:
    """Create user_dir and copy bundled personalities if it's empty."""
    user_dir.mkdir(parents=True, exist_ok=True)
    if any(user_dir.glob("*.md")):
        return
    if not BUNDLED_DIR.exists():
        return
    for src in BUNDLED_DIR.glob("*.md"):
        dst = user_dir / src.name
        if not dst.exists():
            shutil.copy(src, dst)


def load(user_dir: Path, name: str) -> str:
    """Load a personality by name, with fallback to 'default', then to a stub."""
    ensure_personalities(user_dir)
    p = user_dir / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    fallback = user_dir / "default.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8").strip()
    return "You are peek, a tiny popup assistant. Be helpful and concise."


def list_available(user_dir: Path) -> list[str]:
    ensure_personalities(user_dir)
    return sorted(p.stem for p in user_dir.glob("*.md") if p.is_file())
