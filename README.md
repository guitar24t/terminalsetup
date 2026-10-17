# terminalsetup (termgrid)

Arrange terminal windows in a configurable grid on each monitor, using the
desktop area available around the menu bar, panels, and dock. Supports
Terminal.app on macOS and GNOME Terminal on Linux, including GNOME Wayland
through Xwayland.

Requires Python 3.11 or later.

## Setup

On macOS, create a virtual environment and install the Python bindings:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install pyobjc-framework-Cocoa pyobjc-framework-ScriptingBridge
```

Allow control of Terminal when macOS requests Automation permission.

On Linux, install GNOME Terminal, `xrandr`, `gdbus`, and libX11 using your
distribution's packages. Wayland sessions also require Xwayland.

## Usage

```sh
python3 terminalsetup.py --help
python3 terminalsetup.py --list-monitors
python3 terminalsetup.py --dry-run
python3 terminalsetup.py
```

Running the command again reuses and arranges its existing windows, opens missing
ones, and closes those that no longer fit. Override a monitor's layout with
`--grid 'primary=3x2'`, adjust spacing with `--gap 8`, or close the windows opened
by the tool with `--close`.

## Configuration

The bundled `termgrid.toml` provides defaults and example monitor layouts. Keep
your personal settings outside the repository:

```sh
mkdir -p ~/.config/termgrid
cp termgrid.toml ~/.config/termgrid/termgrid.toml
```

Edit that copy using the monitor names reported by `--list-monitors`. Grids use
`COLUMNSxROWS`; `off` leaves a monitor alone. You can also configure a terminal
profile, working directory, and startup command. Use `--config PATH` to select
another file. The default lookup respects `XDG_CONFIG_HOME` when set.

## macOS app

With the virtual environment active, build a double-clickable launcher:

```sh
./build-app.sh
open termgrid.app
```

Rebuild after changing the source, bundled configuration, or Python environment.
The app records the build-time Python interpreter path; keep that interpreter
available. Personal configuration still takes precedence over the bundled file.

Generated app bundles, local environments, and secret files are ignored by Git.
