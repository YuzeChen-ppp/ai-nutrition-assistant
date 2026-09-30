"""中文界面：翻译表完整性、日期格式、以及「界面上不再出现英文 UI 文案」。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from app.i18n import available_languages, current_language, format_date, set_language, tr
from app.translations import DEFAULT_LANGUAGE, TRANSLATIONS

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 这些英文词组是需求里明确要求不要出现的界面文案。
FORBIDDEN_UI_TEXT = (
    "Upload Food Photo",
    "Enter Food Manually",
    "Today's Nutrition",
    "Recommend Next Meal",
    "Today's Meals",
    "Next Meal Recommendation",
    "Recognised food",
    "Confirm",
    "Cancel",
    "Loading",
    "Settings",
    "History",
)

# 只检查真正会被显示的字符串字面量，不检查代码里的标识符
# （例如 ConfirmFoodDialog / HistoryPage）。
_STRING_LITERAL_RE = re.compile(r"([\"'])((?:\\.|(?!\1).)*)\1")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _python_files(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "translations.py"
    ]


class TranslationTableTests(unittest.TestCase):
    def test_default_language_is_chinese(self) -> None:
        self.assertEqual(DEFAULT_LANGUAGE, "zh_CN")
        self.assertEqual(available_languages(), {"zh_CN": "简体中文"})

    def test_every_key_used_in_the_code_exists(self) -> None:
        keys: set[str] = set()
        pattern = re.compile(r"\btr\(\s*[\"']([a-z0-9_.]+)[\"']")
        for path in _python_files(APP_DIR):
            keys.update(pattern.findall(path.read_text(encoding="utf-8")))
        missing = sorted(key for key in keys if key not in TRANSLATIONS["zh_CN"])
        self.assertEqual(missing, [], f"缺少中文文案：{missing}")

    def test_missing_key_falls_back_to_the_key_itself(self) -> None:
        self.assertEqual(tr("does.not.exist"), "does.not.exist")

    def test_placeholder_substitution(self) -> None:
        self.assertEqual(tr("status.saved_items", count=3, kcal="344"), "已保存 3 项，共 344 kcal")

    def test_broken_placeholder_does_not_raise(self) -> None:
        self.assertIn("{count}", tr("status.saved_items"))

    def test_unknown_language_keeps_chinese(self) -> None:
        try:
            self.assertEqual(set_language("en"), "zh_CN")
            self.assertEqual(current_language(), "zh_CN")
        finally:
            set_language(DEFAULT_LANGUAGE)


class DateFormatTests(unittest.TestCase):
    def test_chinese_date(self) -> None:
        self.assertEqual(format_date("2026-09-14"), "2026年9月14日")
        self.assertEqual(format_date("2026-01-05"), "2026年1月5日")

    def test_invalid_date_is_returned_unchanged(self) -> None:
        self.assertEqual(format_date("not-a-date"), "not-a-date")


class NoEnglishUiTests(unittest.TestCase):
    def test_ui_files_contain_no_forbidden_english_text(self) -> None:
        offenders: list[str] = []
        for path in _python_files(APP_DIR / "ui"):
            text = path.read_text(encoding="utf-8")
            for literal in _STRING_LITERAL_RE.findall(text):
                content = literal[1]
                if _IDENTIFIER_RE.match(content):
                    continue  # 类名/模块名之类的标识符，不是界面文案
                for phrase in FORBIDDEN_UI_TEXT:
                    if phrase in content:
                        offenders.append(f"{path.name}: {content[:60]}")
        self.assertEqual(offenders, [], f"界面里仍然有英文文案：{offenders}")

    def test_every_ui_python_file_is_readable_as_utf8(self) -> None:
        for path in _python_files(APP_DIR):
            path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
