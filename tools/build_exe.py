#!/usr/bin/env python3
"""
Build Wincast.exe (Windows) with PyInstaller.

    pip install -e .[ui,build]
    python tools/build_exe.py            # -> dist/Wincast/Wincast.exe
    python tools/build_exe.py --zip      # also dist/Wincast-<version>-win64.zip
    python tools/build_exe.py --zip --installer   # and dist/Wincast-<version>-setup.exe

The installer needs Inno Setup 6.3+ (https://jrsoftware.org/isinfo.php, or
`choco install innosetup`); set ISCC to its ISCC.exe if it isn't found.

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
    ap.add_argument("--installer", action="store_true", help="also build the setup.exe (Inno Setup)")
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
    shipped = []
    if args.zip:
        out = shutil.make_archive(str(ROOT / "dist" / f"Wincast-{version()}-win64"), "zip",
                                  ROOT / "dist", "Wincast")
        shipped.append(Path(out))
        print(f"zipped {out}")
    if args.installer:
        shipped.append(build_installer())
    if shipped:
        import hashlib
        lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in shipped]
        (ROOT / "dist" / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
        print("checksums in dist/SHA256SUMS")


def find_iscc():
    env = os.environ.get("ISCC")
    if env:
        return env
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").exists():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    raise SystemExit("Inno Setup's ISCC.exe not found: install Inno Setup 6 or set ISCC")


def build_installer() -> Path:
    cmd = [find_iscc(), "/Qp", f"/DAppVersion={version()}", str(ROOT / "installer" / "wincast.iss")]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    out = ROOT / "dist" / f"Wincast-{version()}-setup.exe"
    if not out.exists():
        raise SystemExit(f"installer not built: {out}")
    print(f"installer {out}")
    return out


if __name__ == "__main__":
    main()
