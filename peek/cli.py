"""Terminal driver for peek's brain — useful for dogfooding without the GUI.

Run a single conversation in the terminal. Ctrl+D or empty line to flush
and exit; Ctrl+C to abort without flushing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from peek.chat import Chat
from peek.config import CONFIG_PATH, Config
from peek.memory.reflect import reflect
from peek.memory.store import MemoryStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="peek-cli", description="peek terminal driver")
    parser.add_argument("--config", type=Path, default=None,
                        help=f"Path to config (default: {CONFIG_PATH})")
    parser.add_argument("--no-flush", action="store_true",
                        help="Skip the reflect/flush step on exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show reasoning + tool calls inline")
    return parser.parse_args()


def _print_event(kind: str, *args, verbose: bool) -> None:
    if kind == "delta":
        print(args[0], end="", flush=True)
    elif kind == "reasoning":
        if verbose:
            print(f"\033[2m{args[0]}\033[0m", end="", flush=True)
    elif kind == "tool_call":
        name, payload = args
        print(f"\n\033[36m[tool→ {name}({payload})]\033[0m", flush=True)
    elif kind == "tool_result":
        name, result = args
        if verbose:
            preview = result if len(result) <= 200 else result[:200] + "…"
            print(f"\033[36m[← {name}: {preview}]\033[0m", flush=True)
        else:
            print(f"\033[36m[← {name}]\033[0m", flush=True)
    elif kind == "done":
        print()  # newline after final assistant text
    elif kind == "error":
        print(f"\n\033[31merror: {args[0]}\033[0m", file=sys.stderr, flush=True)


async def _amain(args: argparse.Namespace) -> int:
    config = Config.load(args.config) if args.config else Config.load()
    config.write_default_if_missing(args.config)

    config.memory_dir.mkdir(parents=True, exist_ok=True)
    config.personalities_dir.mkdir(parents=True, exist_ok=True)

    store = MemoryStore(config.memory_dir)
    chat = Chat.create(config, store)

    print(f"peek-cli — {config.host}, model={config.model}, "
          f"personality={config.personality}, "
          f"{len(store.list_entries())} memory entries")
    print("(Ctrl+D or empty line to flush+quit, Ctrl+C to abort)\n")

    try:
        while True:
            try:
                user_input = input("\033[33m>\033[0m ").strip()
            except EOFError:
                print()
                break
            if not user_input:
                break
            print("\033[32m●\033[0m ", end="", flush=True)
            async for event in chat.send(user_input):
                _print_event(*event, verbose=args.verbose)
    except KeyboardInterrupt:
        print("\naborted (no flush)", file=sys.stderr)
        return 130

    if args.no_flush:
        print(f"\nskipping flush ({len(chat.scratch)} pending notes)")
        return 0

    print("\nflushing memory…", flush=True)
    result = await reflect(
        config=config, store=store,
        messages=chat.messages, scratch=chat.scratch,
        backend=chat.backend,
    )
    if result.errors:
        for e in result.errors:
            print(f"  ! {e}", file=sys.stderr)
    print(f"flushed: {len(result.applied)} ops applied, {len(result.errors)} errors")
    return 0


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 — top-level fence; print and exit cleanly
        print(f"\nfatal: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
