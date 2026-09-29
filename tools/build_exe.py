#!/usr/bin/env python3
"""
Build Wincast.exe (Windows) with PyInstaller.

    pip install -e .[ui,build]
    python tools/build_exe.py            # -> dist/Wincast/Wincast.exe
    python tools/build_exe.py --zip      # also dist/Wincast-<version>-win64.zip

A folder build ("onedir"), not a single file: it starts faster, and one-file
builds unpack themselves to a temp folder on every launch, which antivirus
tools flag far more often. Run it on Windows: PyInstaller can only build for
the OS it runs on.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def version():
    sys.path.insert(0, str(ROOT / "src"))
    from wincast import __version__
    return __version__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="also zip the folder for sharing")
    args = ap.parse_args()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller isn't installed: pip install -e .[build]")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                         # no console window
        "--name", "Wincast",
        "--icon", str(ROOT / "src" / "wincast" / "resources" / "icon" / "wincast.ico"),
        "--paths", str(ROOT / "src"),
        # the model, item prices and riotgames.pem. Listed explicitly: --collect-data
        # can't see a package that is only on --paths, and silently ships nothing.
        "--add-data", f"{ROOT / 'src' / 'wincast' / 'resources'}{os.pathsep}wincast/resources",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "tools" / "wincast_launcher.py"),
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / "Wincast" / ("Wincast.exe" if sys.platform == "win32" else "Wincast")
    res = ROOT / "dist" / "Wincast" / "_internal" / "wincast" / "resources"
    needed = ("models/fallback.json", "riotgames.pem", "icon/wincast-16.png")
    missing = [f for f in needed if not (res / f).exists()]
    if missing:
        raise SystemExit(f"build is missing bundled files: {missing}")
    print(f"\nbuilt {exe}")
    if args.zip:
        out = shutil.make_archive(str(ROOT / "dist" / f"Wincast-{version()}-win64"), "zip",
                                  ROOT / "dist", "Wincast")
        import hashlib
        digest = hashlib.sha256(Path(out).read_bytes()).hexdigest()
        (ROOT / "dist" / "SHA256SUMS").write_text(f"{digest}  {Path(out).name}\n", encoding="utf-8")
        print(f"zipped {out}\nchecksum in dist/SHA256SUMS")


if __name__ == "__main__":
    main()
