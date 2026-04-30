"""Config for peek — single INI file at ~/.config/peek.conf.

Sections:

    [server]              llama.cpp host/auth
    [model]               default model name + generation knobs
    [memory]              memory dir override
    [personality]         active personality
    [hotkey]              GlobalShortcuts portal binding
    [search]              websearch provider + key

All values have safe fallbacks. Missing file = pure defaults.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "peek"
CONFIG_PATH = CONFIG_DIR / "config.conf"
DEFAULT_MEMORY_DIR = CONFIG_DIR / "memory"
DEFAULT_PERSONALITIES_DIR = CONFIG_DIR / "personalities"


@dataclass
class Config:
    # Server
    host: str = "http://localhost:8080"
    api_key: str = "llama.cpp"
    verify_ssl: bool = True

    # Model + generation
    model: str = "default"
    temperature: float | None = None
    thinking: bool | None = None  # None = server default

    # Memory
    memory_dir: Path = field(default_factory=lambda: DEFAULT_MEMORY_DIR)
    personalities_dir: Path = field(default_factory=lambda: DEFAULT_PERSONALITIES_DIR)
    personality: str = "default"

    # Default preferred_trigger sent to the GlobalShortcuts portal for the
    # toggle action. KDE refuses Alt Gr as a modifier, hence Alt+X — KDE will
    # accept this as the auto-bound default if no shortcut is set yet.
    hotkey: str = "ALT+X"

    # Tools
    search_provider: str = "tavily"  # tavily | brave | searxng
    search_api_key: str = ""
    searxng_url: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        cfg = cls()
        if not path.exists():
            return cfg
        parser = configparser.ConfigParser()
        parser.read(path)

        cfg.host = parser.get("server", "host", fallback=cfg.host)
        cfg.api_key = parser.get("server", "api_key", fallback=cfg.api_key)
        cfg.verify_ssl = parser.getboolean("server", "verify_ssl", fallback=cfg.verify_ssl)

        cfg.model = parser.get("model", "model", fallback=cfg.model)
        if parser.has_option("model", "temperature"):
            cfg.temperature = parser.getfloat("model", "temperature")
        if parser.has_option("model", "thinking"):
            cfg.thinking = parser.getboolean("model", "thinking")

        if parser.has_option("memory", "dir"):
            cfg.memory_dir = Path(parser.get("memory", "dir")).expanduser()
        if parser.has_option("memory", "personalities_dir"):
            cfg.personalities_dir = Path(
                parser.get("memory", "personalities_dir")
            ).expanduser()

        cfg.personality = parser.get("personality", "name", fallback=cfg.personality)
        cfg.hotkey = parser.get("hotkey", "binding", fallback=cfg.hotkey)

        cfg.search_provider = parser.get("search", "provider", fallback=cfg.search_provider)
        cfg.search_api_key = parser.get("search", "api_key", fallback=cfg.search_api_key)
        cfg.searxng_url = parser.get("search", "searxng_url", fallback=cfg.searxng_url)

        return cfg

    def write_default_if_missing(self, path: Path | None = None) -> bool:
        """Write a stub config if none exists. Returns True if written."""
        path = path or CONFIG_PATH
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_CONFIG_TEXT, encoding="utf-8")
        return True


def update_personality(name: str, path: Path | None = None) -> None:
    """Persist a new active personality to the config file in place.

    Preserves all other sections / values; only the [personality] name is
    rewritten.
    """
    path = path or CONFIG_PATH
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)
    if not parser.has_section("personality"):
        parser.add_section("personality")
    parser.set("personality", "name", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


_DEFAULT_CONFIG_TEXT = """\
# peek configuration. All values are optional — defaults shown.

[server]
host = http://localhost:8080
# api_key = llama.cpp
# verify_ssl = true

[model]
model = default
# temperature = 0.7
# thinking = true

[memory]
# dir = ~/.config/peek/memory
# personalities_dir = ~/.config/peek/personalities

[personality]
name = default

[hotkey]
binding = ALT+X

[search]
# provider = tavily
# api_key =
# searxng_url =
"""
