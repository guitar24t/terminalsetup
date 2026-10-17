# terminalsetup (termgrid)

Arrange terminal windows in a configurable grid on each monitor, using the
desktop area available around the menu bar, panels, dock, and taskbar. Supports
Terminal.app on macOS, GNOME Terminal on Linux (including GNOME Wayland through
Xwayland), and PowerShell console windows on Windows.

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

On Windows 10 or 11, install Python (for example with
`winget install Python.Python.3.13`); no other packages are needed. The
terminals run PowerShell 7 (`pwsh`) when it is installed and Windows PowerShell
otherwise. They always open as classic console windows, even when Windows
Terminal is the default terminal app, because Windows Terminal windows cannot
be told apart and arranged individually.

## Usage

```sh
python3 terminalsetup.py --help
python3 terminalsetup.py --list-monitors
python3 terminalsetup.py --dry-run
python3 terminalsetup.py
```

On Windows, run the same commands from PowerShell with `py` in place of
`python3`.

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

On Windows, in PowerShell:

```powershell
New-Item -ItemType Directory -Force ~\.config\termgrid
Copy-Item termgrid.toml ~\.config\termgrid\termgrid.toml
```

Edit that copy using the monitor names reported by `--list-monitors`. Grids use
`COLUMNSxROWS`; `off` leaves a monitor alone. You can also configure a terminal
profile, working directory, and startup command. Use `--config PATH` to select
another file. The default lookup respects `XDG_CONFIG_HOME` when set.

On Windows, monitors are named `DISPLAY1`, `DISPLAY2`, and so on, and `profile`
is not used. A string `command` runs through PowerShell's `-Command` instead of
`sh -c`; a list such as `["cmd.exe"]` runs that program instead of PowerShell.
A `cd` in your PowerShell profile overrides `working_directory`.

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
and Intel (`x86_64`) and standalone Windows executables for `x86_64` and
`arm64`. It attaches a ZIP for each macOS architecture, an executable for each
Windows architecture (`termgrid-VERSION-windows-ARCH.exe`), and `SHA256SUMS`. These builds include Python (and PyObjC on macOS), so recipients
do not need to install them. Tags with a suffix such as `v1.2.3-rc.1` publish a
prerelease. Version tags must use `vMAJOR.MINOR.PATCH`, optionally followed by
prerelease or build metadata.

The apps have ad-hoc signatures and are not notarized with an Apple Developer
certificate. macOS may require approval in System Settings > Privacy & Security
when opening a downloaded app. Allow Terminal Automation permission when asked.

The Windows executable is not code-signed, so SmartScreen may warn when it is
first opened; choose **More info** > **Run anyway**. Double-click it to arrange
the terminals, or run it from PowerShell with the same options as the script.

Run **Release apps** manually from GitHub Actions to test the builds and
download their artifacts without publishing a release. To build a standalone
app locally on macOS:

```sh
python3 -m pip install -r packaging/requirements.txt
mkdir -p build
python3 make-icon.py build/termgrid.icns
TERMGRID_VERSION=v1.2.3 python3 -m PyInstaller --clean --noconfirm packaging/termgrid.spec
```

The distributable app is written to `dist/termgrid.app`. On Windows, in
PowerShell:

```powershell
py -m pip install -r packaging/requirements.txt
$env:TERMGRID_VERSION = "v1.2.3"
py -m PyInstaller --clean --noconfirm packaging/termgrid.spec
```

The executable is written to `dist\termgrid.exe`. Its icon is drawn from
Terminal's on macOS, so the release workflow makes it on a macOS runner. To
include it in a local build, run `python3 make-icon.py build/termgrid.ico` on a
Mac and copy the file to `build\termgrid.ico` first; otherwise the executable
gets Windows' default icon.
