"""Throw-away target used to test whether PyInstaller can package PySide6 here.

Build it with:

    python -m PyInstaller --noconfirm --onedir --console --name MiniQt \
        --distpath dist-mini --workpath build-mini --specpath build-mini \
        tools/mini_qt_check.py

Then run ``dist-mini/MiniQt/MiniQt.exe``. If that prints ``MINI QT OK`` the
toolchain works and the problem is in the application's build options.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import qVersion  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    label = QLabel("hello")
    label.show()
    app.processEvents()
    print(f"MINI QT OK (Qt {qVersion()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
