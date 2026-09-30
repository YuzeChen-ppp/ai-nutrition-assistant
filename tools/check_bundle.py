"""Check a PyInstaller bundle for missing DLL dependencies.

Usage:
    python tools/check_bundle.py dist/AI-Nutrition-Assistant/_internal

For every ``.dll`` / ``.pyd`` in the bundle it reads the PE import table and
reports imports that are neither in the bundle nor available in the system
folders. This is how the missing-Qt-DLL problem was found while packaging.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pefile

SYSTEM_DIRS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
]


def system_dlls() -> set[str]:
    names: set[str] = set()
    for folder in SYSTEM_DIRS:
        if folder.is_dir():
            for entry in folder.glob("*.dll"):
                names.add(entry.name.lower())
    return names


def bundle_files(root: Path) -> tuple[set[str], list[Path]]:
    names: set[str] = set()
    binaries: list[Path] = []
    for path in root.rglob("*"):
        if path.suffix.lower() in {".dll", ".pyd", ".exe"}:
            names.add(path.name.lower())
            binaries.append(path)
    return names, binaries


def imports_of(path: Path) -> list[str]:
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  ! could not read {path.name}: {exc}")
        return []
    names: list[str] = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        names.append(entry.dll.decode("ascii", "replace"))
    pe.close()
    return names


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "dist/AI-Nutrition-Assistant/_internal")
    if not root.is_dir():
        print(f"not a folder: {root}")
        return 1

    present, binaries = bundle_files(root)
    system = system_dlls()
    print(f"bundle : {root}")
    print(f"binaries: {len(binaries)}   system dlls known: {len(system)}")

    missing: dict[str, set[str]] = {}
    for binary in binaries:
        for dependency in imports_of(binary):
            key = dependency.lower()
            if key in present or key in system:
                continue
            if key.startswith("api-ms-win-") or key.startswith("ext-ms-"):
                continue
            missing.setdefault(dependency, set()).add(binary.name)

    if not missing:
        print("OK: every DLL import is present in the bundle or in Windows.")
        return 0

    print(f"\nMISSING ({len(missing)}):")
    for dependency, users in sorted(missing.items()):
        users_text = ", ".join(sorted(users)[:4])
        print(f"  {dependency}  <- {users_text}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
