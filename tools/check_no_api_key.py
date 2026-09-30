"""Verify the API key never reaches the database, the logs or the bundle.

This is the acceptance check for "API key must not appear in DB / log /
packaging output". It reads the local artefacts only; it never contacts the
network and never prints a key.

    python tools/check_no_api_key.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.credentials import default_api_key_store, mask_secret  # noqa: E402

#: Artefacts that must never contain a key, plus the file types worth scanning.
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".ini", ".cfg", ".toml", ".log", ".sql",
    ".example", ".spec", ".yml", ".yaml",
}
BINARY_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".exe", ".dll", ".pyd", ".zip"}


def _real_key() -> tuple[str, str]:
    """The configured key and where it came from ('' when there is none)."""
    for name in ("DEEPSEEK_API_KEY",):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, f"environment variable {name}"
    store = default_api_key_store()
    loaded = store.load()
    value = (loaded.value or "").strip()
    if value:
        return value, loaded.source_label()
    return "", "not configured"


def _scan_bytes(path: Path, needle: bytes) -> bool:
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                if needle in chunk:
                    return True
    except OSError:
        return False
    return False


def _scan_sqlite(path: Path, needle: bytes) -> list[str]:
    """Search every table cell of a SQLite file for the needle."""
    hits: list[str] = []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return hits
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        ]
        for table in tables:
            quoted = '"' + str(table).replace('"', '""') + '"'
            try:
                rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                for value in row:
                    if value is None:
                        continue
                    blob = value if isinstance(value, bytes) else str(value).encode(
                        "utf-8", "ignore"
                    )
                    if needle in blob:
                        hits.append(f"{path.name}:{table}")
    finally:
        connection.close()
    return hits


def main() -> int:
    key, source = _real_key()
    if not key:
        print("没有配置 API Key：无法做泄漏检查（至少 key 肯定不在文件里）。")
        print("提示：先在界面里保存一次 Key，再运行这个脚本。")
        return 0

    print(f"API Key 来源：{source}")
    print(f"用于扫描的形式：{mask_secret(key)}（长度 {len(key)}）")
    print("（脚本本身不会打印完整 Key）")
    print()

    needle = key.encode("utf-8")
    failures: list[str] = []
    scanned = 0
    skipped = 0

    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            skipped += 1
            continue
        # A .env file is the documented fallback storage, so it is allowed to
        # hold the key; it is git-ignored and never packaged.
        if path.name == ".env":
            print(f"  跳过（后备存储，已在 .gitignore 中）：{path.relative_to(PROJECT_ROOT)}")
            continue
        scanned += 1
        if path.suffix.lower() in BINARY_SUFFIXES:
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                failures.extend(_scan_sqlite(path, needle))
            elif _scan_bytes(path, needle):
                failures.append(str(path.relative_to(PROJECT_ROOT)))
        elif path.suffix.lower() in TEXT_SUFFIXES or not path.suffix:
            if _scan_bytes(path, needle):
                failures.append(str(path.relative_to(PROJECT_ROOT)))

    print(f"  已扫描 {scanned} 个文件（跳过 {skipped} 个版本控制/虚拟环境文件）")
    print()

    # The database gets an explicit pass/fail line as well.
    from app.utils.config import db_path

    database = Path(db_path())
    if database.is_file():
        db_hits = _scan_sqlite(database, needle)
        print(f"数据库：{database}")
        print(f"  数据库中出现 Key：{'是 -> ' + str(db_hits) if db_hits else '否'}")
        failures.extend(db_hits)
    else:
        print(f"数据库：{database}（还不存在）")

    log_file = PROJECT_ROOT / "logs" / "app.log"
    if log_file.is_file():
        in_log = _scan_bytes(log_file, needle)
        print(f"日志：{log_file}")
        print(f"  日志中出现 Key：{'是' if in_log else '否'}")
        if in_log:
            failures.append(str(log_file.relative_to(PROJECT_ROOT)))

    for directory in ("dist", "build"):
        folder = PROJECT_ROOT / directory
        if folder.is_dir():
            print(f"打包产物：{folder}（已包含在上面的扫描范围内）")

    print()
    if failures:
        print("结果：失败，以下位置出现了 API Key：")
        for item in dict.fromkeys(failures):
            print(f"  - {item}")
        return 1
    print("结果：通过 —— 数据库、日志和打包产物里都没有 API Key。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
