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

## Releases

Push a version tag to build and publish a GitHub release:

```sh
git tag v1.2.3
git push origin v1.2.3
```

The release workflow builds standalone macOS apps for Apple Silicon (`arm64`)
and Intel (`x86_64`) and attaches a ZIP for each architecture plus `SHA256SUMS`.
These apps include Python and PyObjC, so recipients do not need to install them.
Tags with a suffix such as `v1.2.3-rc.1` publish a prerelease. Version tags must
use `vMAJOR.MINOR.PATCH`, optionally followed by prerelease or build metadata.

The apps have ad-hoc signatures and are not notarized with an Apple Developer
certificate. macOS may require approval in System Settings > Privacy & Security
when opening a downloaded app. Allow Terminal Automation permission when asked.

Run **Release macOS app** manually from GitHub Actions to test the builds and
download their artifacts without publishing a release. To build a standalone
app locally on macOS:

```sh
python3 -m pip install -r packaging/requirements.txt
mkdir -p build
python3 make-icon.py build/termgrid.icns
TERMGRID_VERSION=v1.2.3 python3 -m PyInstaller --clean --noconfirm packaging/termgrid.spec
```

The distributable app is written to `dist/termgrid.app`.
