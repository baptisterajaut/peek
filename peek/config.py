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
from typing import Any

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
    # Pass-through generation options sent as extra_body to the backend.
    # Any key llama.cpp accepts works (repeat_penalty, repeat_last_n, top_p,
    # top_k, min_p, presence_penalty, frequency_penalty, seed, …).
    model_options: dict = field(default_factory=dict)

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

    # Sandbox. read/write/run_shell tools require bwrap by default. Set the
    # flag to True to enable them WITHOUT a sandbox — the agent then has raw
    # filesystem access to /tmp/peek and can shell out arbitrarily on the host.
    unsafe_no_sandbox: bool = False

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

        cfg.unsafe_no_sandbox = parser.getboolean(
            "sandbox",
            "i_dont_care_if_an_agent_wipes_my_files",
            fallback=cfg.unsafe_no_sandbox,
        )

        if parser.has_section("model_options"):
            cfg.model_options = _parse_model_options(parser["model_options"])

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


def _parse_model_options(section) -> dict[str, Any]:
    """Coerce values from a configparser section to bool/int/float/str.

    Empty values are skipped. The dict is passed straight through to the
    backend as extra_body — any key llama.cpp accepts works.
    """
    out: dict[str, Any] = {}
    for key in section:
        raw = section.get(key, "").strip()
        if not raw:
            continue
        low = raw.lower()
        if low in ("true", "false"):
            out[key] = low == "true"
            continue
        try:
            out[key] = int(raw)
            continue
        except ValueError:
            pass
        try:
            out[key] = float(raw)
            continue
        except ValueError:
            pass
        out[key] = raw
    return out


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

# read_file / write_file / run_shell tools. By default they require
# `bwrap` (bubblewrap) on PATH so script execution is confined to
# /tmp/peek with no network. If bwrap is missing the tools are not
# registered. Flip the flag below to enable them WITHOUT any sandbox —
# the agent gets raw shell access to your machine.
[sandbox]
# i_dont_care_if_an_agent_wipes_my_files = false

# Pass-through llama.cpp generation options. Anything in this section is
# forwarded as extra_body. Common knobs:
#   repeat_penalty = 1.1
#   repeat_last_n = 64
#   top_p = 0.95
#   top_k = 40
#   min_p = 0.05
#   presence_penalty = 0
#   frequency_penalty = 0
#   seed = -1
[model_options]
"""
