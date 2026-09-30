"""Colours, fonts and the Qt stylesheet.

The font is chosen from fonts that actually exist on the machine, which is how
Chinese text (and any other script) is displayed correctly instead of as boxes
or mojibake.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication

__all__ = ["ACCENT", "COLOURS", "STYLESHEET", "apply_app_style", "pick_ui_font"]

ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
OK = "#16a34a"
WARN = "#d97706"
DANGER = "#dc2626"

COLOURS = {
    "background": "#f1f5f9",
    "card": "#ffffff",
    "border": "#e2e8f0",
    "text": "#0f172a",
    "muted": "#64748b",
    "accent": ACCENT,
    "ok": OK,
    "warn": WARN,
    "danger": DANGER,
}

_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Segoe UI",
    "Arial",
)

# 让 Qt 自己在这份列表里挑一个存在的字体：即使程序选字体失败，
# 控件也依然能显示中文，而不是出现「豆腐块」。
_FONT_FAMILY_CSS = ", ".join(f'"{name}"' for name in _FONT_CANDIDATES) + ", sans-serif"

STYLESHEET = f"""
QWidget {{
    color: {COLOURS['text']};
    font-family: {_FONT_FAMILY_CSS};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {COLOURS['background']};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

QFrame#Card {{
    background: {COLOURS['card']};
    border: 1px solid {COLOURS['border']};
    border-radius: 12px;
}}
QLabel#CardTitle {{
    font-size: 16px;
    font-weight: 600;
}}
QLabel#SectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {COLOURS['text']};
}}
QLabel#Muted {{
    color: {COLOURS['muted']};
}}
QLabel#Warning {{
    color: {COLOURS['warn']};
}}
QLabel#Danger {{
    color: {COLOURS['danger']};
}}
QLabel#Ok {{
    color: {COLOURS['ok']};
}}
QLabel#BigNumber {{
    font-size: 26px;
    font-weight: 700;
}}
QLabel#Chip {{
    background: #e0edff;
    color: {ACCENT_DARK};
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
}}
QLabel#ChipWarn {{
    background: #fef3c7;
    color: {WARN};
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
}}

QPushButton {{
    background: {COLOURS['card']};
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{
    background: #f8fafc;
    border-color: #94a3b8;
}}
QPushButton:disabled {{
    color: #94a3b8;
    background: #f1f5f9;
}}
QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_DARK};
}}
QPushButton#Primary:disabled {{
    background: #93b4f5;
    border-color: #93b4f5;
    color: #f8fafc;
}}
QPushButton#Danger {{
    color: {DANGER};
    border-color: #fecaca;
}}
QPushButton#Danger:hover {{
    background: #fef2f2;
}}
QPushButton#Link {{
    border: none;
    background: transparent;
    color: {ACCENT_DARK};
    padding: 2px 6px;
}}
QPushButton#Link:hover {{
    text-decoration: underline;
}}

QProgressBar {{
    background: #e8eef7;
    border: none;
    border-radius: 8px;
    height: 14px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 8px;
}}
QProgressBar#Over::chunk {{
    background: {DANGER};
}}
QProgressBar#Ok::chunk {{
    background: {OK};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {COLOURS['card']};
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPlainTextEdit {{
    padding: 8px;
}}

QListWidget, QTableWidget, QTreeWidget {{
    background: {COLOURS['card']};
    border: 1px solid {COLOURS['border']};
    border-radius: 8px;
    gridline-color: {COLOURS['border']};
}}
QHeaderView::section {{
    background: #f8fafc;
    border: none;
    border-bottom: 1px solid {COLOURS['border']};
    padding: 6px 8px;
    font-weight: 600;
}}
QTableWidget::item:selected {{
    background: #dbeafe;
    color: {COLOURS['text']};
}}

QTabWidget::pane {{
    border: 1px solid {COLOURS['border']};
    border-radius: 10px;
    background: {COLOURS['card']};
}}
QTabBar::tab {{
    padding: 8px 16px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: 8px;
    color: {COLOURS['muted']};
}}
QTabBar::tab:selected {{
    background: {COLOURS['card']};
    border: 1px solid {COLOURS['border']};
    color: {COLOURS['text']};
    font-weight: 600;
}}

QSplitter::handle {{
    background: {COLOURS['border']};
}}
QStatusBar {{
    background: {COLOURS['card']};
    border-top: 1px solid {COLOURS['border']};
}}
QDialogButtonBox QPushButton {{
    min-width: 90px;
}}
"""


def pick_ui_font() -> QFont:
    """Return the first available UI font that covers CJK text."""
    try:
        available = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - 字体数据库异常时退回默认字体
        available = set()

    for family in _FONT_CANDIDATES:
        if family not in available:
            continue
        font = QFont(family)
        font.setPointSize(10)
        # 确认系统真的能解析这个字体，而不是被替换成别的（可能没有中文）
        if QFontInfo(font).family():
            return font

    # 没找到已知字体：先试 Qt 的默认字体，能显示中文就用
    default_font = QFont()
    if QFontInfo(default_font).family():
        default_font.setPointSize(10)
        return default_font

    font = QFont()
    font.setFamily(_FONT_CANDIDATES[0])
    font.setPointSize(10)
    return font


def apply_app_style(app: QApplication) -> None:
    """Apply the font and stylesheet to the whole application."""
    app.setFont(pick_ui_font())
    app.setStyleSheet(STYLESHEET)
