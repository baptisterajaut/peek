#!/bin/bash
# peek launcher — resolve symlinks, ensure venv, run daemon.
# Suitable for KDE Autostart (Settings → Autostart → Add Login Script).

set -e

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

VENV="$SCRIPT_DIR/.venv"
PYPROJECT="$SCRIPT_DIR/pyproject.toml"
MARKER="$VENV/.deps-installed"

# Create venv on first run.
if [ ! -d "$VENV" ]; then
    echo "[peek] creating venv…" >&2
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -e "$SCRIPT_DIR"
    touch "$MARKER"
fi

# Reinstall if pyproject changed (new deps, version bumps).
if [ ! -f "$MARKER" ] || [ "$PYPROJECT" -nt "$MARKER" ]; then
    echo "[peek] syncing deps…" >&2
    "$VENV/bin/pip" install -q -e "$SCRIPT_DIR"
    touch "$MARKER"
fi

exec "$VENV/bin/peek" "$@"
