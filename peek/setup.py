"""Interactive first-run wizard.

Walks the user through: server URL → probe models → pick model → pick
personality → thinking mode → hotkey description, then writes
~/.config/peek/config.conf.

Invoked explicitly via `peek setup`, or auto-triggered by the daemon when
the config doesn't exist and stdin is a TTY.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from peek.config import CONFIG_PATH, Config
from peek.personality import ensure_personalities, list_available


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{text}{suffix}: ").strip()
    return raw or default


def _prompt_yn(text: str, default: bool = True) -> bool:
    yn = "Y/n" if default else "y/N"
    raw = input(f"{text} [{yn}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _select_numbered(label: str, items: list[str], current: str | None = None) -> str:
    print(f"\n{label}:")
    default_idx = 1
    for i, item in enumerate(items, 1):
        marker = "  (current)" if item == current else ""
        print(f"  {i}. {item}{marker}")
        if item == current:
            default_idx = i
    while True:
        raw = input(f"Pick [{default_idx}]: ").strip() or str(default_idx)
        try:
            idx = int(raw)
            if 1 <= idx <= len(items):
                return items[idx - 1]
        except ValueError:
            pass
        print(f"Enter a number between 1 and {len(items)}.")


async def _list_models(host: str, verify_ssl: bool) -> list[str]:
    async with httpx.AsyncClient(verify=verify_ssl, timeout=8.0) as client:
        resp = await client.get(f"{host.rstrip('/')}/v1/models")
        resp.raise_for_status()
        data = resp.json()
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def run_setup(force: bool = False) -> int:
    """Run the interactive wizard. Returns 0 on success, non-zero on cancel."""
    print("peek setup")
    print("──────────")

    if CONFIG_PATH.exists() and not force:
        print(f"\nConfig already exists at {CONFIG_PATH}.")
        if not _prompt_yn("Overwrite?", default=False):
            return 0
        print()

    existing = Config.load() if CONFIG_PATH.exists() else Config()

    host = _prompt("llama.cpp server URL", existing.host)
    if not host.startswith(("http://", "https://")):
        host = "http://" + host

    verify_ssl = existing.verify_ssl
    if host.startswith("https"):
        verify_ssl = _prompt_yn("Verify SSL certs?", default=existing.verify_ssl)

    print(f"\nProbing {host}…")
    try:
        models = asyncio.run(_list_models(host, verify_ssl))
    except Exception as e:  # noqa: BLE001
        print(f"  ! couldn't reach server: {type(e).__name__}: {e}")
        if not _prompt_yn("Continue anyway?", default=False):
            return 1
        models = []

    if models:
        model = _select_numbered("Available models", models, current=existing.model)
    else:
        model = _prompt("Model name (server unreachable, type manually)", existing.model)

    # Personalities — copy bundled defaults if user dir empty.
    ensure_personalities(existing.personalities_dir)
    personalities = list_available(existing.personalities_dir)
    if len(personalities) <= 1:
        personality = personalities[0] if personalities else "default"
        print(f"\nPersonality: {personality}")
    else:
        personality = _select_numbered("Personalities", personalities, current=existing.personality)

    print()
    thinking_default = (existing.thinking is not False)
    thinking = _prompt_yn("Enable model thinking (Qwen3, DeepSeek-R1)?", default=thinking_default)

    hotkey = _prompt(
        "\nGlobal hotkey description (registered via portal — KDE may ask "
        "you to confirm or pick another)",
        existing.hotkey,
    )

    print()
    search_provider = ""
    if _prompt_yn("Configure web search now?", default=False):
        search_provider = _select_numbered(
            "Provider", ["tavily", "brave", "searxng"], current=existing.search_provider,
        )
        if search_provider in ("tavily", "brave"):
            api_key = _prompt(f"{search_provider} API key", existing.search_api_key)
        else:
            api_key = ""
        searxng_url = ""
        if search_provider == "searxng":
            searxng_url = _prompt("SearXNG base URL (e.g. https://searx.example.org)",
                                   existing.searxng_url)
    else:
        search_provider = existing.search_provider
        api_key = existing.search_api_key
        searxng_url = existing.searxng_url

    text = _render_config(
        host=host,
        verify_ssl=verify_ssl,
        model=model,
        thinking=thinking,
        personality=personality,
        hotkey=hotkey,
        search_provider=search_provider,
        api_key=api_key,
        searxng_url=searxng_url,
    )

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    print(f"\n✓ saved to {CONFIG_PATH}")
    print("\nNext: start the daemon with `peek`. Hotkey is auto-registered via")
    print("the GlobalShortcuts portal — your DE may prompt you to confirm.")
    return 0


def _render_config(
    *, host: str, verify_ssl: bool, model: str, thinking: bool,
    personality: str, hotkey: str, search_provider: str,
    api_key: str, searxng_url: str,
) -> str:
    out = [
        "# peek configuration — generated by `peek setup`",
        "",
        "[server]",
        f"host = {host}",
        f"verify_ssl = {str(verify_ssl).lower()}",
        "",
        "[model]",
        f"model = {model}",
        f"thinking = {str(thinking).lower()}",
        "",
        "[personality]",
        f"name = {personality}",
        "",
        "[hotkey]",
        f"binding = {hotkey}",
        "",
        "[search]",
    ]
    if search_provider:
        out.append(f"provider = {search_provider}")
    else:
        out.append("# provider = tavily")
    if api_key:
        out.append(f"api_key = {api_key}")
    else:
        out.append("# api_key =")
    if searxng_url:
        out.append(f"searxng_url = {searxng_url}")
    else:
        out.append("# searxng_url =")
    out.append("")
    return "\n".join(out)


def main() -> int:
    try:
        return run_setup(force=True)
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    except EOFError:
        print("\nstdin closed — cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
