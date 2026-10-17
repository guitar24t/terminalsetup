#!/usr/bin/env python3
"""Fill each monitor's work area with a grid of terminal windows.

Each monitor can have its own number of columns and rows (see termgrid.toml).
The terminals tile the area left free by the top bar, dock and taskbar exactly,
with no overlap and no gaps.

On Linux the terminals are GNOME Terminal windows.  On a GNOME Wayland session,
native Wayland windows cannot be positioned by another program, so the
terminals are opened by a dedicated gnome-terminal-server running under
Xwayland (GDK_BACKEND=x11).  The shells inside them are not affected by that
setting.

On macOS the terminals are Terminal.app windows, driven over Apple Events with
PyObjC (pip install pyobjc-framework-Cocoa pyobjc-framework-ScriptingBridge).
The first run asks for permission to control Terminal.  build-app.sh wraps
this script in a double-clickable termgrid.app.

On Windows the terminals are PowerShell console windows (PowerShell 7 when it
is installed, otherwise Windows PowerShell), moved with the Win32 API through
ctypes.  They are started through conhost.exe, because Windows 11 would
otherwise hand them to Windows Terminal, whose windows cannot be told apart.
"""

import argparse
import ctypes
import ctypes.util
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import tomllib

try:  # macOS only
    import AppKit
    import ScriptingBridge
except ImportError:
    AppKit = ScriptingBridge = None

ROLE_PREFIX = "termgrid-"
SERVER_PATHS = ["/usr/libexec/gnome-terminal-server", "/usr/lib/gnome-terminal-server",
                "/usr/lib/gnome-terminal/gnome-terminal-server"]
# Ubuntu wraps the real client in a script that spawns its own server when
# given --app-id, so call the real client directly when it exists.
CLIENT_PATHS = ["/usr/bin/gnome-terminal.real", "/usr/bin/gnome-terminal"]


# --------------------------------------------------------------------------
# Minimal Xlib binding (ctypes), enough to find, inspect and move windows.
# --------------------------------------------------------------------------

class XSizeHints(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_long),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("min_width", ctypes.c_int), ("min_height", ctypes.c_int),
                ("max_width", ctypes.c_int), ("max_height", ctypes.c_int),
                ("width_inc", ctypes.c_int), ("height_inc", ctypes.c_int),
                ("min_aspect_x", ctypes.c_int), ("min_aspect_y", ctypes.c_int),
                ("max_aspect_x", ctypes.c_int), ("max_aspect_y", ctypes.c_int),
                ("base_width", ctypes.c_int), ("base_height", ctypes.c_int),
                ("win_gravity", ctypes.c_int)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class XEvent(ctypes.Union):
    _fields_ = [("xclient", XClientMessageEvent), ("pad", ctypes.c_long * 24)]


PMinSize = 1 << 4
PResizeInc = 1 << 6
ClientMessage = 33
SubstructureRedirectMask = 1 << 20
SubstructureNotifyMask = 1 << 19

_c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
_c_int_p = ctypes.POINTER(ctypes.c_int)
_c_uint_p = ctypes.POINTER(ctypes.c_uint)
_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class X11:
    def __init__(self):
        path = ctypes.util.find_library("X11")
        if not path:
            sys.exit("termgrid: libX11 not found")
        lib = self.lib = ctypes.cdll.LoadLibrary(path)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, _c_ulong_p, _c_int_p, _c_ulong_p, _c_ulong_p,
            ctypes.POINTER(ctypes.c_void_p)]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XGetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                          ctypes.POINTER(XSizeHints), ctypes.POINTER(ctypes.c_long)]
        lib.XSetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XSizeHints)]
        lib.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        lib.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, _c_ulong_p, _c_int_p, _c_int_p,
                                     _c_uint_p, _c_uint_p, _c_uint_p, _c_uint_p]
        lib.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                              ctypes.c_int, ctypes.c_int, _c_int_p, _c_int_p, _c_ulong_p]
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                   ctypes.POINTER(XEvent)]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XSetErrorHandler.argtypes = [_XErrorHandler]

        self.dpy = lib.XOpenDisplay(None)
        if not self.dpy:
            sys.exit("termgrid: cannot open X display (is DISPLAY set?)")
        # Windows can vanish between listing and querying them; ignore the
        # resulting BadWindow errors instead of letting Xlib exit the process.
        self._error_handler = _XErrorHandler(lambda dpy, err: 0)
        lib.XSetErrorHandler(self._error_handler)
        self.root = lib.XDefaultRootWindow(self.dpy)
        self._atoms = {}

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.lib.XInternAtom(self.dpy, name.encode(), False)
        return self._atoms[name]

    def _property(self, win, name):
        """Return (format, raw pointer, item count), or None if unset."""
        actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
        nitems, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self.lib.XGetWindowProperty(
            self.dpy, win, self.atom(name), 0, 0x7FFFFFFF, False, 0,
            actual_type, fmt, nitems, after, data)
        if status != 0 or not data.value:
            return None
        return fmt.value, data, nitems.value

    def cardinals(self, win, name):
        """Read a 32-bit format property (CARDINAL, WINDOW, ...) as a list of ints."""
        prop = self._property(win, name)
        if prop is None:
            return []
        fmt, data, count = prop
        # Xlib hands 32-bit items back as C longs.
        values = list(ctypes.cast(data, ctypes.POINTER(ctypes.c_long))[:count]) if fmt == 32 else []
        self.lib.XFree(data)
        return values

    def string(self, win, name):
        prop = self._property(win, name)
        if prop is None:
            return ""
        fmt, data, count = prop
        value = ctypes.string_at(data, count).decode(errors="replace") if fmt == 8 else ""
        self.lib.XFree(data)
        return value

    def normal_hints(self, win):
        hints, supplied = XSizeHints(), ctypes.c_long()
        if not self.lib.XGetWMNormalHints(self.dpy, win, ctypes.byref(hints), ctypes.byref(supplied)):
            return None
        return hints

    def drop_resize_increments(self, win):
        """Stop the window manager snapping this window's size to whole character cells."""
        hints = self.normal_hints(win)
        if hints is not None and hints.flags & PResizeInc:
            hints.flags &= ~PResizeInc
            self.lib.XSetWMNormalHints(self.dpy, win, ctypes.byref(hints))

    def move_resize(self, win, x, y, w, h):
        self.lib.XMoveResizeWindow(self.dpy, win, x, y, w, h)

    def geometry(self, win):
        """Return the window's (x, y, width, height) in root coordinates, or None."""
        root, x, y = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, h, border, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not self.lib.XGetGeometry(self.dpy, win, root, x, y, w, h, border, depth):
            return None
        child = ctypes.c_ulong()
        if not self.lib.XTranslateCoordinates(self.dpy, win, self.root, 0, 0, x, y, child):
            return None
        return x.value, y.value, w.value, h.value

    def _send_to_wm(self, win, message, data):
        event = XEvent()
        msg = event.xclient
        msg.type, msg.send_event, msg.display = ClientMessage, True, self.dpy
        msg.window, msg.message_type, msg.format = win, self.atom(message), 32
        msg.data[:] = (list(data) + [0] * 5)[:5]
        self.lib.XSendEvent(self.dpy, self.root, False,
                            SubstructureRedirectMask | SubstructureNotifyMask, ctypes.byref(event))

    def unmaximize(self, win):
        """The window manager ignores size requests from maximized or fullscreen windows."""
        states = set(self.cardinals(win, "_NET_WM_STATE"))
        blocking = [self.atom(name) for name in ("_NET_WM_STATE_MAXIMIZED_VERT",
                                                 "_NET_WM_STATE_MAXIMIZED_HORZ",
                                                 "_NET_WM_STATE_FULLSCREEN")]
        for atom in blocking:
            if atom in states:
                self._send_to_wm(win, "_NET_WM_STATE", [0, atom, 0, 2])  # 0 = remove

    def close_window(self, win):
        """Ask the window manager to close a window, as if its close button was clicked."""
        self._send_to_wm(win, "_NET_CLOSE_WINDOW", [int(time.time() * 1000) & 0xFFFFFFFF, 2])

    def sync(self):
        self.lib.XSync(self.dpy, False)

    def client_windows(self):
        return self.cardinals(self.root, "_NET_CLIENT_LIST")

    def termgrid_windows(self):
        """Map WM_WINDOW_ROLE -> window id for every terminal termgrid opened."""
        windows = {}
        for win in self.client_windows():
            role = self.string(win, "WM_WINDOW_ROLE")
            if role.startswith(ROLE_PREFIX):
                windows[role] = win
        return windows


# --------------------------------------------------------------------------
# Minimal Win32 binding (ctypes), enough to find, inspect and move windows.
# Fixed-width types stand in for ctypes.wintypes so this imports anywhere.
# --------------------------------------------------------------------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32),
                ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]

    def rect(self):
        return Rect(self.left, self.top, self.right - self.left, self.bottom - self.top)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", ctypes.c_uint32), ("szDevice", ctypes.c_wchar * 32)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_int32),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
MONITORINFOF_PRIMARY = 1
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9
WM_CLOSE = 0x0010
TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class Win32:
    def __init__(self):
        user32 = self.user32 = ctypes.WinDLL("user32")
        kernel32 = self.kernel32 = ctypes.WinDLL("kernel32")
        self.dwmapi = ctypes.WinDLL("dwmapi")
        self._monitor_proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(RECT), ctypes.c_ssize_t)
        self._window_proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, self._monitor_proc,
                                               ctypes.c_ssize_t]
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFOEXW)]
        user32.EnumWindows.argtypes = [self._window_proc, ctypes.c_ssize_t]
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        for name in ("IsWindow", "IsIconic", "IsZoomed"):
            getattr(user32, name).argtypes = [ctypes.c_void_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.dwmapi.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                      ctypes.c_uint32]
        kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        # Work in physical pixels everywhere.  Otherwise Windows scales the
        # coordinates of monitors whose display scaling is not 100%.
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))

    def monitors(self):
        """MONITORINFOEXW for every monitor, in the order Windows lists them."""
        found = []

        def visit(monitor, dc, rect, data):
            info = MONITORINFOEXW(cbSize=ctypes.sizeof(MONITORINFOEXW))
            if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                found.append(info)
            return True

        self.user32.EnumDisplayMonitors(None, None, self._monitor_proc(visit), 0)
        return found

    def class_name(self, win):
        buf = ctypes.create_unicode_buffer(256)
        return buf.value if self.user32.GetClassNameW(win, buf, len(buf)) else ""

    def owner(self, win):
        pid = ctypes.c_uint32()
        self.user32.GetWindowThreadProcessId(win, ctypes.byref(pid))
        return pid.value

    def windows(self, class_name):
        """Map window handle -> owning process id for every top-level window of a class."""
        found = {}

        def visit(win, data):
            if self.class_name(win) == class_name:
                found[win] = self.owner(win)
            return True

        self.user32.EnumWindows(self._window_proc(visit), 0)
        return found

    def parent_processes(self):
        """Map process id -> parent process id for every running process."""
        snapshot = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return {}
        parents = {}
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        more = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            more = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        self.kernel32.CloseHandle(snapshot)
        return parents

    def is_window(self, win):
        return bool(self.user32.IsWindow(win))

    def window_rect(self, win):
        """The window's (x, y, width, height), including its invisible resize borders, or None."""
        rect = RECT()
        return rect.rect() if self.user32.GetWindowRect(win, ctypes.byref(rect)) else None

    def frame(self, win):
        """The part of the window that is drawn, without the invisible resize borders, or None."""
        rect = RECT()
        if self.dwmapi.DwmGetWindowAttribute(win, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                             ctypes.sizeof(rect)) != 0:
            return None
        return rect.rect()

    def restore(self, win):
        """Minimized windows ignore moves, and maximized ones snap back to the whole work area."""
        if self.user32.IsIconic(win) or self.user32.IsZoomed(win):
            self.user32.ShowWindow(win, SW_RESTORE)

    def move_resize(self, win, x, y, w, h):
        self.user32.SetWindowPos(win, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)

    def close_window(self, win):
        """Ask a window to close, as if its close button was clicked."""
        self.user32.PostMessageW(win, WM_CLOSE, 0, 0)


# --------------------------------------------------------------------------
# Monitors and the space available on each of them.
# --------------------------------------------------------------------------

class Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    def intersect(self, other):
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2 = min(self.x + self.w, other.x + other.w)
        y2 = min(self.y + self.h, other.y + other.h)
        return Rect(x1, y1, max(0, x2 - x1), max(0, y2 - y1))

    @property
    def area(self):
        return self.w * self.h

    def __eq__(self, other):
        return (self.x, self.y, self.w, self.h) == (other.x, other.y, other.w, other.h)

    def __repr__(self):
        return f"{self.w}x{self.h}+{self.x}+{self.y}"


class Monitor:
    def __init__(self, index, name, primary, rect):
        self.index, self.name, self.primary, self.rect = index, name, primary, rect
        self.workarea = rect
        self.grid = None  # (columns, rows), or None to leave the monitor alone


MONITOR_LINE = re.compile(r"^\s*(\d+):\s+\+?(\*?)(\S+)\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")


def list_monitors(x11):
    out = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True, check=True).stdout
    monitors = []
    for line in out.splitlines():
        m = MONITOR_LINE.match(line)
        if m:
            index, star, name, w, h, x, y = m.groups()
            monitors.append(Monitor(int(index), name, star == "*",
                                    Rect(int(x), int(y), int(w), int(h))))
    if not monitors:
        sys.exit("termgrid: xrandr reported no monitors")

    # Mutter publishes one work area per monitor (the monitor minus the top
    # bar, dock and other panels).  Other window managers only publish a
    # single bounding work area, which is clipped to each monitor instead.
    desktop = (x11.cardinals(x11.root, "_NET_CURRENT_DESKTOP") or [0])[0]
    flat = x11.cardinals(x11.root, f"_GTK_WORKAREAS_D{desktop}")
    if not flat:
        flat = x11.cardinals(x11.root, "_NET_WORKAREA")[4 * desktop:4 * desktop + 4]
    areas = [Rect(*flat[i:i + 4]) for i in range(0, len(flat) - 3, 4)]
    for mon in monitors:
        clipped = [area.intersect(mon.rect) for area in areas]
        best = max(clipped, key=lambda r: r.area, default=None)
        if best is not None and best.area > 0:
            mon.workarea = best
    return monitors


# --------------------------------------------------------------------------
# Configuration.
# --------------------------------------------------------------------------

def default_config_path():
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    user = os.path.join(xdg, "termgrid", "termgrid.toml")
    if os.path.exists(user):
        return user
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "termgrid.toml")


def parse_grid(value, where):
    """Accept "3x2", {columns = 3, rows = 2} or "off"; return (columns, rows) or None."""
    if isinstance(value, dict):
        cols, rows = value.get("columns"), value.get("rows")
    elif isinstance(value, str) and value.strip().lower() in ("off", "none", "skip", "0", "0x0"):
        return None
    elif isinstance(value, str) and re.fullmatch(r"\s*\d+\s*[xX]\s*\d+\s*", value):
        cols, rows = (int(n) for n in re.split(r"[xX]", value))
    else:
        sys.exit(f"termgrid: {where}: expected \"COLUMNSxROWS\" (e.g. \"3x2\") or \"off\", got {value!r}")
    if not (isinstance(cols, int) and isinstance(rows, int)) or cols < 0 or rows < 0:
        sys.exit(f"termgrid: {where}: columns and rows must be whole numbers, got {value!r}")
    return None if cols == 0 or rows == 0 else (cols, rows)


def monitor_matches(key, mon):
    key = str(key).strip()
    return (key.lower() == mon.name.lower() or key == str(mon.index)
            or (key.lower() == "primary" and mon.primary))


def assign_grids(monitors, config, overrides, default_override):
    layouts = config.get("monitors", {})
    if not isinstance(layouts, dict):
        sys.exit("termgrid: [monitors] must be a table of MONITOR = \"COLUMNSxROWS\" entries")
    default = default_override if default_override is not None else config.get("default", "1x1")

    for key in layouts:
        if not any(monitor_matches(key, mon) for mon in monitors):
            names = ", ".join(mon.name for mon in monitors)
            print(f"termgrid: note: monitor {key!r} from the config is not connected (have: {names})",
                  file=sys.stderr)

    for mon in monitors:
        value, where = default, "default"
        # Prefer the most specific key: connector name, then index, then "primary".
        for key, layout in sorted(layouts.items(),
                                  key=lambda kv: str(kv[0]).lower() == "primary"):
            if monitor_matches(key, mon):
                value, where = layout, f"monitors.{key}"
                break
        for key, layout in overrides:
            if monitor_matches(key, mon):
                value, where = layout, f"--grid {key}"
        mon.grid = parse_grid(value, where)


def split(start, length, parts, gap):
    """Split [start, start+length) into `parts` spans separated by `gap` pixels, with no rounding gaps."""
    usable = length - gap * (parts + 1)
    edges = [usable * i // parts for i in range(parts + 1)]
    return [(start + gap * (i + 1) + edges[i], edges[i + 1] - edges[i]) for i in range(parts)]


def plan_cells(monitors, gap):
    """Return {role: Rect} for every terminal that should exist."""
    cells = {}
    for mon in monitors:
        if mon.grid is None:
            continue
        cols, rows = mon.grid
        area = mon.workarea
        for c, (x, w) in enumerate(split(area.x, area.w, cols, gap)):
            for r, (y, h) in enumerate(split(area.y, area.h, rows, gap)):
                cells[f"{ROLE_PREFIX}{mon.name}-c{c + 1}r{r + 1}"] = Rect(x, y, w, h)
    return cells


# --------------------------------------------------------------------------
# The dedicated Xwayland gnome-terminal-server.
# --------------------------------------------------------------------------

def find_executable(candidates, what):
    for path in candidates:
        if os.access(path, os.X_OK):
            return path
    sys.exit(f"termgrid: {what} not found (looked in {', '.join(candidates)})")


def bus_name_owned(name):
    result = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
         "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner", name],
        capture_output=True, text=True)
    return "true" in result.stdout


def ensure_server(app_id):
    """Start an X11 gnome-terminal-server owning `app_id` unless one is already running."""
    if bus_name_owned(app_id):
        return
    server = find_executable(SERVER_PATHS, "gnome-terminal-server")
    env = dict(os.environ, GDK_BACKEND="x11")
    subprocess.Popen([server, "--app-id", app_id], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if bus_name_owned(app_id):
            return
        time.sleep(0.05)
    sys.exit("termgrid: gnome-terminal-server did not start")


def open_terminals(app_id, roles, config):
    client = find_executable(CLIENT_PATHS, "gnome-terminal")
    args = [client, "--app-id", app_id]
    if config.get("profile"):
        args.append(f"--profile={config['profile']}")
    if config.get("working_directory"):
        args.append(f"--working-directory={os.path.expanduser(config['working_directory'])}")
    for role in roles:
        args += ["--window", f"--role={role}"]
    command = config.get("command")
    if command:
        args += ["--"] + (command if isinstance(command, list) else ["sh", "-c", command])
    # The shells inherit the client's environment, so keep the backend override out of it.
    env = dict(os.environ)
    env.pop("GDK_BACKEND", None)
    subprocess.run(args, env=env, check=True)


def wait_for_windows(x11, roles, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        found = x11.termgrid_windows()
        if all(role in found for role in roles) or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


# --------------------------------------------------------------------------
# Placement.
# --------------------------------------------------------------------------

def frame_extents(x11, win):
    """Invisible client-side shadow around a GTK window, as (left, right, top, bottom)."""
    extents = x11.cardinals(win, "_GTK_FRAME_EXTENTS")
    return tuple(extents) if len(extents) == 4 else (0, 0, 0, 0)


def outer_rect(x11, win, cell):
    """X window geometry whose visible part (excluding the shadow) exactly covers `cell`."""
    left, right, top, bottom = frame_extents(x11, win)
    return Rect(cell.x - left, cell.y - top, cell.w + left + right, cell.h + top + bottom)


def place_windows(desktop, placements, attempts=8):
    """Move every (window, cell) into place and retry until each one stays put.

    GTK may answer the first resize by snapping back to its own character-cell
    size, so geometry is checked after each round and stragglers are moved again.
    """
    pending = dict(placements)
    for _ in range(attempts):
        for win, cell in pending.items():
            desktop.move(win, cell)
        desktop.sync()
        time.sleep(0.25)
        for win in list(pending):
            actual = desktop.visible_rect(win)
            if actual is None or actual == pending[win]:
                del pending[win]
        if not pending:
            return {}
    return pending


def check_minimum_sizes(x11, placements):
    """Warn about cells smaller than a terminal can be (they would overlap their neighbours)."""
    for win, cell in placements.items():
        hints = x11.normal_hints(win)
        if hints is None or not hints.flags & PMinSize:
            continue
        left, right, top, bottom = frame_extents(x11, win)
        min_w, min_h = hints.min_width - left - right, hints.min_height - top - bottom
        if cell.w < min_w or cell.h < min_h:
            print(f"termgrid: warning: cells of {cell.w}x{cell.h} are smaller than the terminal minimum "
                  f"of {min_w}x{min_h}; use fewer columns/rows or a smaller font", file=sys.stderr)
            return


# --------------------------------------------------------------------------
# Platforms.  Each one finds monitors and opens, closes and moves terminals.
# --------------------------------------------------------------------------

class GnomeTerminal:
    """GNOME Terminal windows on X11, or on Xwayland under GNOME Wayland."""

    def __init__(self):
        for tool in ("xrandr", "gdbus"):
            if not shutil.which(tool):
                sys.exit(f"termgrid: '{tool}' is required but not installed")
        self.x11 = X11()

    def monitors(self):
        return list_monitors(self.x11)

    def windows(self):
        return self.x11.termgrid_windows()

    def close(self, windows):
        close_windows(self.x11, windows)

    def launch(self, roles, config):
        ensure_server(APP_ID)
        open_terminals(APP_ID, roles, config)
        return wait_for_windows(self.x11, roles)

    def check_minimum_sizes(self, placements):
        check_minimum_sizes(self.x11, placements)

    def move(self, win, cell):
        self.x11.unmaximize(win)
        self.x11.drop_resize_increments(win)
        target = outer_rect(self.x11, win, cell)
        self.x11.move_resize(win, target.x, target.y, target.w, target.h)

    def sync(self):
        self.x11.sync()

    def visible_rect(self, win):
        """The window's geometry without its client-side shadow, or None if it is gone."""
        actual = self.x11.geometry(win)
        if actual is None:
            return None
        x, y, w, h = actual
        left, right, top, bottom = frame_extents(self.x11, win)
        return Rect(x + left, y + top, w - left - right, h - top - bottom)


MAC_STATE_PATH = os.path.expanduser("~/Library/Application Support/termgrid/terminals.json")
SAVE_NO = int.from_bytes(b"no  ", "big")  # Terminal's "save options" enumerator


def startup_script(config):
    """Shell command typed into a new Terminal.app window to apply the terminal options."""
    steps = []
    if config.get("working_directory"):
        steps.append("cd " + shlex.quote(os.path.expanduser(config["working_directory"])))
    command = config.get("command")
    if command:
        # Like `gnome-terminal -- COMMAND`, the command takes the shell's place.
        steps.append("exec " + shlex.join(command if isinstance(command, list) else ["sh", "-c", command]))
    # Terminal echoes the typed command; clear it away before anything else prints.
    return " && ".join(["clear"] + steps) if steps else ""


class MacTerminal:
    """Terminal.app windows on macOS.

    Terminal.app has nothing like a window role to tag its windows with (the
    shell overwrites a tab's custom title), so the window id and tty of each
    terminal termgrid opens are remembered in MAC_STATE_PATH instead.
    """

    def __init__(self):
        if ScriptingBridge is None:
            sys.exit("termgrid: PyObjC is required on macOS "
                     "(pip install pyobjc-framework-Cocoa pyobjc-framework-ScriptingBridge)")
        self.app = ScriptingBridge.SBApplication.applicationWithBundleIdentifier_("com.apple.Terminal")
        if self.app is None:
            sys.exit("termgrid: Terminal.app not found")
        try:
            with open(MAC_STATE_PATH) as f:
                self.state = json.load(f)  # role -> {"window": id, "tty": path}
        except (OSError, ValueError):
            self.state = {}

    def _save(self):
        os.makedirs(os.path.dirname(MAC_STATE_PATH), exist_ok=True)
        with open(MAC_STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2)

    def _window(self, win):
        return self.app.windows().objectWithID_(win)

    def _live_ids(self):
        # Checking isRunning first keeps a query from launching Terminal.
        if not self.app.isRunning():
            return set()
        return {int(win) for win in self.app.windows().arrayByApplyingSelector_("id")}

    def _ttys(self, win):
        return [str(tty) for tty in self._window(win).tabs().arrayByApplyingSelector_("tty")]

    def monitors(self):
        screens = AppKit.NSScreen.screens()
        # Cocoa measures up from the bottom-left corner of the primary screen
        # (always the first one); Terminal measures down from its top-left corner.
        top = screens[0].frame().size.height

        def flip(frame):
            (x, y), (w, h) = frame
            return Rect(round(x), round(top - y - h), round(w), round(h))

        monitors = []
        for index, screen in enumerate(screens):
            mon = Monitor(index, str(screen.localizedName()), index == 0, flip(screen.frame()))
            mon.workarea = flip(screen.visibleFrame())  # without the menu bar and Dock
            monitors.append(mon)
        return monitors

    def windows(self):
        """Map role -> window id for every terminal termgrid opened that is still open."""
        live = self._live_ids()
        state = {role: entry for role, entry in self.state.items()
                 if entry["window"] in live and entry["tty"] in self._ttys(entry["window"])}
        if state != self.state:
            self.state = state
            self._save()
        return {role: entry["window"] for role, entry in self.state.items()}

    def close(self, windows, timeout=5):
        for win in windows:
            self._window(win).closeSaving_savingIn_(SAVE_NO, None)
        deadline = time.monotonic() + timeout
        while set(windows) & self._live_ids() and time.monotonic() < deadline:
            time.sleep(0.05)
        left = set(windows) & self._live_ids()
        if left:
            print(f"termgrid: note: {len(left)} terminal(s) did not close; Terminal is probably asking "
                  f"whether to terminate the processes running in them", file=sys.stderr)
        self.windows()

    def launch(self, roles, config):
        settings = None
        if config.get("profile"):
            names = [str(name) for name in self.app.settingsSets().arrayByApplyingSelector_("name")]
            if config["profile"] not in names:
                sys.exit(f"termgrid: Terminal has no profile {config['profile']!r} (have: {', '.join(names)})")
            settings = self.app.settingsSets().objectWithName_(config["profile"])
        script = startup_script(config)
        for role in roles:
            tab = self.app.doScript_in_(script, None)  # no target tab: open a new window
            if tab is None:
                sys.exit("termgrid: could not open a Terminal window; allow it under System Settings > "
                         "Privacy & Security > Automation")
            if settings is not None:
                tab.setCurrentSettings_(settings)
            tty = str(tab.tty())
            win = next((win for win in self._live_ids() if tty in self._ttys(win)), None)
            if win is not None:
                self.state[role] = {"window": win, "tty": tty}
        self._save()
        return self.windows()

    def check_minimum_sizes(self, placements):
        pass  # Terminal.app does not publish its minimum size; place_windows reports any shortfall.

    def move(self, win, cell):
        window = self._window(win)
        if window.miniaturized():
            window.setMiniaturized_(False)
        window.setBounds_(((cell.x, cell.y), (cell.w, cell.h)))

    def sync(self):
        pass  # Apple Events are answered before they return.

    def visible_rect(self, win):
        if win not in self._live_ids():
            return None
        (x, y), (w, h) = self._window(win).bounds()
        return Rect(round(x), round(y), round(w), round(h))


SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
CONHOST = os.path.join(SYSTEM32, "conhost.exe")
WINDOWS_POWERSHELL = os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe")
CONSOLE_CLASS = "ConsoleWindowClass"
WINDOWS_STATE_PATH = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                  "termgrid", "terminals.json")


def console_command(config):
    """Command line run in each console window: PowerShell, or the configured command."""
    command = config.get("command")
    if isinstance(command, list):
        return command
    shell = shutil.which("pwsh") or WINDOWS_POWERSHELL
    # Like `gnome-terminal -- sh -c COMMAND`, the command takes the shell's place.
    return [shell, "-Command", command] if command else [shell]


class PowerShellConsole:
    """PowerShell in console (conhost) windows on Windows.

    Windows 11 hands new consoles to the default terminal app, which is usually
    Windows Terminal, so each window is opened by starting conhost.exe directly.
    A console window reports the shell inside it as its owner, rather than
    conhost, so the shell's parent process ties a window back to its launch.
    The window handle and shell process id of each terminal termgrid opens are
    remembered in WINDOWS_STATE_PATH.
    """

    def __init__(self):
        self.win32 = Win32()
        try:
            with open(WINDOWS_STATE_PATH) as f:
                self.state = json.load(f)  # role -> {"window": handle, "pid": shell process id}
        except (OSError, ValueError):
            self.state = {}

    def _save(self):
        os.makedirs(os.path.dirname(WINDOWS_STATE_PATH), exist_ok=True)
        with open(WINDOWS_STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2)

    def monitors(self):
        monitors = []
        for index, info in enumerate(self.win32.monitors()):
            name = info.szDevice.removeprefix("\\\\.\\")  # \\.\DISPLAY1 -> DISPLAY1
            mon = Monitor(index, name, bool(info.dwFlags & MONITORINFOF_PRIMARY), info.rcMonitor.rect())
            mon.workarea = info.rcWork.rect()  # without the taskbar
            monitors.append(mon)
        return monitors

    def windows(self):
        """Map role -> window handle for every terminal termgrid opened that is still open."""
        consoles = self.win32.windows(CONSOLE_CLASS)
        state = {role: entry for role, entry in self.state.items()
                 if consoles.get(entry["window"]) == entry["pid"]}
        if state != self.state:
            self.state = state
            self._save()
        return {role: entry["window"] for role, entry in self.state.items()}

    def close(self, windows, timeout=5):
        for win in windows:
            self.win32.close_window(win)
        deadline = time.monotonic() + timeout
        while any(self.win32.is_window(win) for win in windows) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.windows()

    def launch(self, roles, config, timeout=15):
        command = console_command(config)
        cwd = os.path.expanduser(config["working_directory"]) if config.get("working_directory") else None
        hosts = {}  # conhost process id -> (process, role)
        for role in roles:
            try:
                proc = subprocess.Popen([CONHOST, *command], cwd=cwd)
            except OSError as err:
                sys.exit(f"termgrid: cannot open a console window: {err}")
            hosts[proc.pid] = proc, role
        deadline = time.monotonic() + timeout
        while True:
            parents = self.win32.parent_processes()
            for win, pid in self.win32.windows(CONSOLE_CLASS).items():
                proc, role = hosts.pop(parents.get(pid), (None, None))
                if role is not None:
                    self.state[role] = {"window": win, "pid": pid}
            # A conhost that has exited will never show a window.
            hosts = {pid: host for pid, host in hosts.items() if host[0].poll() is None}
            if not hosts or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self._save()
        return self.windows()

    def check_minimum_sizes(self, placements):
        pass  # conhost does not publish its minimum size; place_windows reports any shortfall.

    def move(self, win, cell):
        self.win32.restore(win)
        outer, drawn = self.win32.window_rect(win), self.win32.frame(win)
        if outer is None or drawn is None:
            return
        # The resize borders are invisible but still part of the window, so grow
        # the window by them to make its drawn part cover the cell.
        left, top = drawn.x - outer.x, drawn.y - outer.y
        right, bottom = outer.x + outer.w - drawn.x - drawn.w, outer.y + outer.h - drawn.y - drawn.h
        self.win32.move_resize(win, cell.x - left, cell.y - top, cell.w + left + right, cell.h + top + bottom)

    def sync(self):
        pass  # SetWindowPos returns once the window has handled the move.

    def visible_rect(self, win):
        return self.win32.frame(win) if self.win32.is_window(win) else None


# --------------------------------------------------------------------------
# Command line.
# --------------------------------------------------------------------------

APP_ID = "org.termgrid.Terminal"


def close_windows(x11, windows, timeout=5):
    for win in windows:
        x11.close_window(win)
    x11.sync()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not set(windows) & set(x11.client_windows()):
            return
        time.sleep(0.05)


def print_monitors(monitors):
    for mon in monitors:
        grid = "off" if mon.grid is None else f"{mon.grid[0]}x{mon.grid[1]}"
        print(f"{mon.index}: {mon.name}{' (primary)' if mon.primary else ''}  "
              f"monitor {mon.rect}  usable {mon.workarea}  grid {grid}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fill each monitor's free desktop area with a grid of terminal windows.",
        epilog="Running it again re-tiles the existing terminals, opening missing ones and "
               "closing ones that no longer fit the layout.")
    parser.add_argument("-c", "--config", help="config file (default: ~/.config/termgrid/termgrid.toml, "
                                               "else termgrid.toml next to this script)")
    parser.add_argument("-g", "--grid", action="append", default=[], metavar="MONITOR=CxR",
                        help="override one monitor's grid, e.g. DP-7=3x2, 1=2x2, primary=4x1 or DP-12=off "
                             "(repeatable)")
    parser.add_argument("-d", "--default", metavar="CxR",
                        help="grid for monitors the config does not mention")
    parser.add_argument("--gap", type=int, help="pixels between terminals and around the edges")
    parser.add_argument("--reset", action="store_true", help="close existing terminals and open fresh ones")
    parser.add_argument("--close", action="store_true", help="close every terminal termgrid opened and exit")
    parser.add_argument("-l", "--list-monitors", action="store_true",
                        help="show monitors, their usable area and assigned grid, then exit")
    parser.add_argument("-n", "--dry-run", action="store_true", help="print the planned layout and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    if sys.platform == "darwin":
        desktop = MacTerminal()
    elif sys.platform == "win32":
        desktop = PowerShellConsole()
    else:
        desktop = GnomeTerminal()

    if args.close:
        desktop.close(list(desktop.windows().values()))
        return

    config = {}
    config_path = args.config or default_config_path()
    if os.path.exists(config_path):
        with open(config_path, "rb") as f:
            try:
                config = tomllib.load(f)
            except tomllib.TOMLDecodeError as err:
                sys.exit(f"termgrid: {config_path}: {err}")
    elif args.config:
        sys.exit(f"termgrid: config file {config_path} does not exist")

    overrides = []
    for item in args.grid:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"termgrid: --grid expects MONITOR=COLUMNSxROWS, got {item!r}")
        overrides.append((key, value))

    monitors = desktop.monitors()
    assign_grids(monitors, config, overrides, args.default)
    gap = args.gap if args.gap is not None else int(config.get("gap", 0))
    cells = plan_cells(monitors, gap)

    if args.list_monitors:
        print_monitors(monitors)
        return
    if args.dry_run:
        print_monitors(monitors)
        for role, cell in cells.items():
            print(f"  {role[len(ROLE_PREFIX):]:<20} {cell}")
        return

    existing = desktop.windows()
    stale = [win for role, win in existing.items() if args.reset or role not in cells]
    if stale:
        desktop.close(stale)
        existing = {role: win for role, win in existing.items() if win not in stale}

    missing = [role for role in cells if role not in existing]
    if missing:
        existing = desktop.launch(missing, config)
        not_found = [role for role in missing if role not in existing]
        if not_found:
            sys.exit(f"termgrid: {len(not_found)} terminal window(s) never appeared")

    placements = {existing[role]: cell for role, cell in cells.items()}
    desktop.check_minimum_sizes(placements)
    unplaced = place_windows(desktop, placements)
    if unplaced:
        for win, cell in unplaced.items():
            print(f"termgrid: warning: window {win:#x} wanted {cell}, "
                  f"got {desktop.visible_rect(win) or 'nothing'}", file=sys.stderr)
        sys.exit(1)

    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        desktop.app.activate()


if __name__ == "__main__":
    main()
