"""Build a distributable macOS app or Windows executable with its own Python runtime."""

import os
from pathlib import Path
import re
import sys

root = Path(SPECPATH).parent
tag = os.environ.get("TERMGRID_VERSION", "v0.0.0")
version = re.fullmatch(r"v(\d+\.\d+\.\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", tag)
if version is None:
    raise SystemExit("TERMGRID_VERSION must be a version tag such as v1.2.3 or v1.2.3-rc.1")

analysis = Analysis(
    [str(root / "terminalsetup.py")],
    datas=[(str(root / "termgrid.toml"), ".")],
)
archive = PYZ(analysis.pure)

if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

    numbers = tuple(int(n) for n in version[1].split(".")) + (0,)
    # A single termgrid.exe.  It is a console program so that its options and
    # messages work from PowerShell; a double-click gives it a console of its own.
    executable = EXE(
        archive,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="termgrid",
        console=True,
        icon="NONE",
        version=VSVersionInfo(
            ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
            kids=[
                StringFileInfo([StringTable("040904B0", [
                    StringStruct("FileDescription", "Arrange PowerShell windows in a grid on each monitor"),
                    StringStruct("FileVersion", version[1]),
                    StringStruct("InternalName", "termgrid"),
                    StringStruct("OriginalFilename", "termgrid.exe"),
                    StringStruct("ProductName", "termgrid"),
                    StringStruct("ProductVersion", tag.removeprefix("v")),
                ])]),
                VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
            ],
        ),
    )
else:
    executable = EXE(
        archive,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="termgrid",
        console=False,
        argv_emulation=False,
        target_arch=os.environ.get("TARGET_ARCH"),
    )
    collection = COLLECT(
        executable,
        analysis.binaries,
        analysis.datas,
        name="termgrid",
    )
    app = BUNDLE(
        collection,
        name="termgrid.app",
        icon=str(root / "build" / "termgrid.icns"),
        bundle_identifier="local.termgrid",
        version=version[1],
        info_plist={
            "CFBundleShortVersionString": version[1],
            "NSAppleEventsUsageDescription": "termgrid opens and arranges Terminal windows.",
        },
    )
