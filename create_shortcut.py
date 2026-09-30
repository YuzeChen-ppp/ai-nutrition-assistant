"""Create a Windows desktop shortcut for the packaged application.

Usage (inside the virtual environment):

    python create_shortcut.py                 # auto-detect dist/AI-Nutrition-Assistant
    python create_shortcut.py --target "C:\\path\\to\\AI-Nutrition-Assistant.exe"
    python create_shortcut.py --name "AI Nutrition Assistant"

The Desktop folder is looked up through the Windows shell API, so it also works
when the Desktop has been moved to OneDrive or another drive.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "AI-Nutrition-Assistant"
DEFAULT_SHORTCUT_NAME = "AI Nutrition Assistant"


def desktop_directory() -> Path:
    """Return the real Desktop folder (Windows shell), with fallbacks."""
    path = _desktop_via_shell_api()
    if path is not None:
        return path
    path = _desktop_via_powershell()
    if path is not None:
        return path
    return Path.home() / "Desktop"


def _desktop_via_shell_api() -> Path | None:
    """Use SHGetKnownFolderPath(FOLDERID_Desktop)."""
    if not sys.platform.startswith("win"):
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id = GUID(
        0xB4BFCC3A,
        0xDB2C,
        0x424C,
        (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
    )
    pointer = ctypes.c_wchar_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)
        )
    except OSError:
        return None
    if result != 0 or not pointer.value:
        return None
    value = pointer.value
    try:
        ctypes.windll.ole32.CoTaskMemFree(pointer)
    except OSError:  # pragma: no cover - freeing is best effort
        pass
    return Path(value)


def _desktop_via_powershell() -> Path | None:
    try:
        output = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Environment]::GetFolderPath('Desktop')",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    value = output.stdout.strip()
    return Path(value) if value else None


def find_executable(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    candidates = (
        ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe",
        ROOT / "dist" / f"{APP_NAME}.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def create_shortcut(target: Path, shortcut: Path, *, icon: Path | None = None) -> None:
    """Create the .lnk file through the WScript.Shell COM object."""
    icon_path = icon or target
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$link = $shell.CreateShortcut('{shortcut}')\n"
        f"$link.TargetPath = '{target}'\n"
        f"$link.WorkingDirectory = '{target.parent}'\n"
        f"$link.IconLocation = '{icon_path}'\n"
        "$link.Description = 'AI Nutrition Assistant - AI food logging and nutrition analysis'\n"
        "$link.Save()\n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "PowerShell could not create the shortcut:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a desktop shortcut.")
    parser.add_argument("--target", help="Path to the .exe to launch.")
    parser.add_argument("--name", default=DEFAULT_SHORTCUT_NAME, help="Shortcut file name.")
    parser.add_argument("--desktop", help="Desktop folder (only if auto-detection fails).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not sys.platform.startswith("win"):
        print("This script creates Windows shortcuts and must run on Windows.")
        return 1

    target = find_executable(args.target)
    if target is None:
        print("Could not find the built application.")
        print("Build it first:  python build.py")
        print("Or pass the path explicitly:  python create_shortcut.py --target \"...\"")
        return 1

    desktop = Path(args.desktop).expanduser() if args.desktop else desktop_directory()
    if not desktop.is_dir():
        print(f"Desktop folder not found: {desktop}")
        return 1

    shortcut = desktop / f"{args.name}.lnk"
    try:
        create_shortcut(target, shortcut)
    except RuntimeError as exc:
        print(exc)
        return 1

    print("Shortcut created.")
    print(f"  shortcut : {shortcut}")
    print(f"  target   : {target}")
    print("Double-click the shortcut on your desktop to start the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
