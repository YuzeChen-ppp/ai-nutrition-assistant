"""Build the Windows executable with PyInstaller.

Usage (inside the virtual environment):

    python build.py                # one-folder build (recommended, most stable)
    python build.py --onefile      # single .exe file (slower to start)
    python build.py --clean        # delete build/ and dist/ first
    python build.py --no-trim      # keep every Qt module (much bigger)

The result is written to ``dist/``.

Why the build environment matters
---------------------------------
PyInstaller resolves missing DLLs by searching ``PATH``. If another program on
the machine ships a same-named library, that copy can end up inside the bundle
and break the app at startup. This actually happened during development: a
Poppler installation on ``PATH`` provided ``icuuc.dll`` (ICU 78), while Qt6Core
links against the small ICU library that Windows ships in ``System32``
(unversioned ``ucnv_open``). The frozen app then failed with
``ImportError: DLL load failed while importing QtCore: WinError 127``.

This script therefore (1) builds with a sanitised ``PATH`` and (2) removes
Windows-provided duplicates from the finished bundle before handing it over.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "AI-Nutrition-Assistant"
ENTRY_POINT = ROOT / "main.py"
ICON = ROOT / "assets" / "icon.ico"

# Qt modules this app never uses. Trimming them turns a ~400 MB build into a
# far smaller one. Verify after building (the script tells you how).
TRIM_MODULES = (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtTextToSpeech",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtHttpServer",
    "matplotlib",
    "IPython",
    "tkinter",
    "numpy",
)

# DLLs that Windows itself provides and that must never be bundled: a
# third-party copy shadows the system one and breaks Qt at startup.
WINDOWS_PROVIDED_DLLS = ("icuuc.dll", "icuin.dll")
WINDOWS_PROVIDED_PATTERNS = ("icudt*.dll",)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the AI Nutrition Assistant EXE.")
    parser.add_argument("--onefile", action="store_true", help="Build a single .exe file.")
    parser.add_argument("--clean", action="store_true", help="Delete build/ and dist/ first.")
    parser.add_argument("--no-trim", action="store_true", help="Do not exclude unused Qt modules.")
    parser.add_argument("--console", action="store_true", help="Keep a console window (debugging).")
    return parser.parse_args(argv)


def build(args: argparse.Namespace) -> int:
    if args.clean:
        for folder in (ROOT / "build", ROOT / "dist"):
            if folder.exists():
                print(f"removing {folder}")
                try:
                    shutil.rmtree(folder)
                except PermissionError as exc:
                    print(
                        "\nCould not delete the previous build output "
                        f"({exc.filename or folder}).\n"
                        "The app is probably still running - close "
                        f"{APP_NAME}.exe and run the build again.",
                        file=sys.stderr,
                    )
                    return 1
                except OSError as exc:
                    print(f"\nCould not delete {folder}: {exc}", file=sys.stderr)
                    return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--icon",
        str(ICON),
        "--add-data",
        f"{ROOT / 'assets'}{os.pathsep}assets",
        "--onefile" if args.onefile else "--onedir",
        "--console" if args.console else "--windowed",
    ]
    if not args.no_trim:
        for module in TRIM_MODULES:
            command += ["--exclude-module", module]
    command.append(str(ENTRY_POINT))

    environment = clean_environment()
    print("running:", " ".join(command))
    print("PATH sanitised for the build (see build.py docstring)")
    result = subprocess.run(command, cwd=ROOT, check=False, env=environment)
    if result.returncode != 0:
        print("\nBuild failed. See the PyInstaller output above.", file=sys.stderr)
        return result.returncode

    output_dir = ROOT / "dist" / (APP_NAME if not args.onefile else "")
    executable = (
        ROOT / "dist" / f"{APP_NAME}.exe"
        if args.onefile
        else ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
    )
    prepare_config_files(executable)
    bundle_root = (ROOT / "dist") if args.onefile else (executable.parent / "_internal")
    removed = clean_bundle(bundle_root)

    print("\nBuild finished.")
    print(f"  executable : {executable}")
    print(f"  folder     : {output_dir}")
    if removed:
        print("  cleaned    : " + ", ".join(path.name for path in removed))
        print("               (Windows-provided libraries; a wrong copy on PATH would break Qt)")
    if args.onefile:
        print("  note       : a one-file build cannot be cleaned afterwards - the sanitised")
        print("               PATH during the build is what protects it.")
    print("\nNext steps:")
    print(f"  1. Put your key in: {executable.parent / '.env'}")
    print("  2. Test it        : double-click the .exe, or run:")
    print(f'       "{executable}" --self-test')
    print("  3. Create the desktop shortcut: python create_shortcut.py")
    return 0


def clean_environment() -> dict[str, str]:
    """Return an environment whose PATH cannot leak third-party DLLs."""
    environment = dict(os.environ)
    keep: list[str] = []
    for entry in (
        Path(sys.executable).parent,
        Path(sys.base_prefix),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",
        Path(os.environ.get("SystemRoot", r"C:\Windows")),
    ):
        text = str(entry)
        if text and text not in keep and Path(text).is_dir():
            keep.append(text)
    environment["PATH"] = os.pathsep.join(keep)
    return environment


def clean_bundle(folder: Path) -> list[Path]:
    """Delete Windows-provided DLLs that PyInstaller may have picked up."""
    removed: list[Path] = []
    if not folder.is_dir():
        return removed
    targets = [folder / name for name in WINDOWS_PROVIDED_DLLS]
    for pattern in WINDOWS_PROVIDED_PATTERNS:
        targets.extend(folder.glob(pattern))
    for path in targets:
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def prepare_config_files(executable: Path) -> None:
    """Put ``.env.example`` (and, if it has a key, your ``.env``) next to the EXE."""
    target_dir = executable.parent
    example = ROOT / ".env.example"
    if example.is_file():
        shutil.copyfile(example, target_dir / ".env.example")

    env_file = target_dir / ".env"
    project_env = ROOT / ".env"
    if has_api_key(project_env) and not has_api_key(env_file):
        shutil.copyfile(project_env, env_file)
        print("copied your .env (with the API key) next to the executable")
        return
    if not env_file.exists():
        env_file.write_text(
            "# 一般不需要手动编辑这个文件：双击程序后，在「设置 → AI 设置」里输入\n"
            "# DeepSeek API Key 即可（会保存到 Windows 凭据管理器）。\n"
            "# 只有当凭据管理器不可用时，程序才会把 Key 写到这个 .env 文件里。\n"
            "# 获取 API Key：https://platform.deepseek.com\n"
            "DEEPSEEK_API_KEY=\n",
            encoding="utf-8",
        )


def has_api_key(env_file: Path) -> bool:
    """True when ``env_file`` exists and contains a non-empty API key."""
    if not env_file.is_file():
        return False
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == "DEEPSEEK_API_KEY" and value.strip().strip("\"'"):
                return True
    except OSError:
        return False
    return False


if __name__ == "__main__":
    raise SystemExit(build(parse_args()))
