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

# IPC subcommands (toggle/show/hide/quit/setup/help) talk to the running
# daemon — they can run anywhere.
if [ $# -gt 0 ]; then
    exec "$VENV/bin/peek" "$@"
fi

# Daemon mode — anchor the cgroup. KDE's portal-kde derives its shortcut
# component name from the cgroup of the calling process. Launching peek.sh
# from Konsole vs Dolphin vs KRunner produces three different cgroups, so
# the alt+x binding ends up split across components and silently fails.
# systemd-run --user wraps us in a deterministic `app-peek@<uuid>.service`,
# giving the portal a stable app id ("peek") forever. It returns
# immediately (the unit runs detached), so the launching shell gets its
# prompt back. Logs land in journald: `journalctl --user -u 'app-peek@*'`.
if [[ "$(cat /proc/self/cgroup)" =~ app-peek@ ]]; then
    exec "$VENV/bin/peek"
fi

UUID=$(tr -d - < /proc/sys/kernel/random/uuid)
exec systemd-run --user --quiet --collect \
    --unit="app-peek@${UUID}.service" \
    "$VENV/bin/peek"
