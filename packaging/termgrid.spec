"""Build a distributable macOS app with its own Python runtime."""

import os
from pathlib import Path
import re

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
