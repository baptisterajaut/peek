# peek

*Basically [ochat](https://github.com/baptisterajaut/ollama-chat) but on the side.*

Tiny popup AI assistant. Hotkey → small floating window → ask → close. The model curates its own memory between sessions.

Linux first (KDE/Wayland via GlobalShortcuts portal). llama.cpp server backend.

## Status

Early. Brain (backend + memory + tools + chat orchestrator + reflect) lands first, GUI after.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# First time? Run the wizard (auto-triggered on first `peek` if interactive):
peek setup

# Daemon — system tray + popup + IPC:
peek

# Terminal driver — dogfood the brain without the GUI:
peek-cli

# IPC commands (bind one of these to your global hotkey in KDE / GNOME / sway):
peek toggle    # show/hide popup
peek show
peek hide
peek quit
```

### Hotkey

peek auto-registers a `toggle` shortcut via the freedesktop GlobalShortcuts
portal (`org.freedesktop.portal.GlobalShortcuts`) on first launch. The
preferred trigger comes from `[hotkey] binding` in your config — KDE will
either accept it or ask you to confirm/pick another.

If the portal is unavailable (older compositor, GNOME without portal
support), bind `peek toggle` manually:

- **KDE Plasma**: System Settings → Shortcuts → Custom Shortcuts → Edit → New → Global Shortcut → Command/URL → `peek toggle`
- **GNOME**: Settings → Keyboard → Custom Shortcut → command `peek toggle`
- **sway/i3/hyprland**: bind directly in the config file

### Autostart

`peek.sh` is a self-installing wrapper: it bootstraps the venv on first run,
syncs deps if `pyproject.toml` changed, then execs the daemon.

**KDE**: System Settings → Autostart → Add → Login Script → pick `peek.sh`.

**Other DEs**: copy `peek.desktop` into `~/.config/autostart/` after
substituting `PEEK_SCRIPT_PATH` with the absolute path to `peek.sh`:

```bash
sed "s|PEEK_SCRIPT_PATH|$(pwd)/peek.sh|" peek.desktop > ~/.config/autostart/peek.desktop
```

## Layout

Everything lives under `~/.config/peek/`:

- `config.conf` — INI, server + model + hotkey + search provider
- `memory/` — `MEMORY.md` index + one `.md` per entry (rsync-friendly)
- `personalities/` — `.md` files, system-prompt presets
