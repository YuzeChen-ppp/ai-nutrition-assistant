"""购买条件（purchase preferences）：解析用户输入、默认常见食材、违规检测。

这里回答的是「我现在方便买到什么」，而不是「我家里有什么」：
用户用自然语言写「我附近超市可以买到鸡胸肉、鸡蛋、牛奶」，程序把这句话
拆成干净的食材列表，再交给 AI 作为推荐的硬约束。
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Sequence

from app.i18n import tr

__all__ = [
    "BUDGETS",
    "COUNTRIES",
    "DEFAULT_EASY_INGREDIENTS",
    "EQUIPMENT_OPTIONS",
    "STORE_OPTIONS",
    "budget_label",
    "country_label",
    "difficulty_label",
    "find_inconvenient_matches",
    "normalise_ingredient",
    "split_ingredient_text",
]

# 国家/地区：combo 显示中文，发给 AI 的是英文全名（模型更熟悉）
COUNTRIES: tuple[tuple[str, str], ...] = (
    ("United Kingdom", "英国"),
    ("United States", "美国"),
    ("China", "中国"),
    ("", "其他 / 不指定"),
)

STORE_OPTIONS: tuple[str, ...] = (
    "Tesco",
    "Sainsbury's",
    "Lidl",
    "Aldi",
    "Asda",
    "Waitrose",
    "Costco",
    "中国超市",
    "本地市场",
)

EQUIPMENT_OPTIONS: tuple[str, ...] = (
    "微波炉",
    "电饭锅",
    "炒锅 / 平底锅",
    "烤箱",
    "空气炸锅",
    "电煮锅",
    "只有热水壶",
)

BUDGETS: tuple[tuple[str, str], ...] = (
    ("low", "低预算"),
    ("normal", "普通"),
    ("unlimited", "不限制"),
)

# 用户没有填写时使用的默认清单：普通大型超市常见的食材。
# 刻意不包含藜麦、牛油果、羽衣甘蓝、特殊奶酪这类小众食材。
DEFAULT_EASY_INGREDIENTS: tuple[str, ...] = (
    "鸡蛋",
    "鸡胸肉",
    "牛肉",
    "猪肉",
    "牛奶",
    "酸奶",
    "米饭",
    "土豆",
    "面包",
    "意大利面",
    "燕麦",
    "香蕉",
    "苹果",
    "西兰花",
    "胡萝卜",
    "番茄",
    "洋葱",
    "菠菜",
)

# 拆句时要丢掉的「客套话」，避免它们混进食材名里
_FILLERS = (
    "我附近超市可以买到",
    "附近超市可以买到",
    "我附近可以买到",
    "我通常可以买到",
    "我一般可以买到",
    "我可以买到",
    "我能买到",
    "我可以买",
    "我能买",
    "超市可以买到",
    "超市能买到",
    "可以买到",
    "能买到",
    "我方便购买",
    "我方便买到",
    "方便购买",
    "方便买到",
    "我常买",
    "我经常买",
    "我平时买",
    "我不方便购买",
    "我不方便买",
    "我买不到",
    "我不想购买",
    "我不想买",
    "我买不到也不吃",
    "不方便购买",
    "不容易买到",
    "我附近买不到",
    "常见的",
    "等等",
)

# 作为分隔符的连词
_CONJUNCTIONS = ("以及", "还有", "或者", "和", "与", "及", "或")

_SEPARATORS = re.compile(r"[,，、;；:：/|\\\n\r\t（）()\[\]【】]+")
# 中文之间的空格算分隔符（"藜麦 羽衣甘蓝"），但英文里的空格不算
# （"chicken breast" 必须保持一个整体）。
_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])[ \u3000]+(?=[\u4e00-\u9fff])")
_TRAILING_JUNK = re.compile(r"^[\s・•·\-—~～.。!！?？\"'“”]+|[\s・•·\-—~～.。!！?？\"'“”]+$")
_FULL_WIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 拆完以后剩下的连接词/语气词，不是食材
_STOP_WORDS = {
    "等",
    "等等",
    "什么的",
    "之类",
    "以及",
    "和",
    "或",
    "或者",
    "还有",
    "其他",
    "其它",
    "之类的东西",
}


def split_ingredient_text(text: str | None) -> list[str]:
    """把用户写的一句话拆成食材列表。

    支持「鸡胸肉、鸡蛋、牛奶」「chicken breast, eggs」「西兰花和胡萝卜」
    以及整句自然语言（会自动去掉「我附近超市可以买到」这类客套话）。
    """
    if not text:
        return []
    cleaned = str(text).translate(_FULL_WIDTH_DIGITS)
    cleaned = _CJK_SPACE.sub("、", cleaned)
    for filler in _FILLERS:
        cleaned = cleaned.replace(filler, "、")
    for word in _CONJUNCTIONS:
        cleaned = cleaned.replace(word, "、")
    parts = _SEPARATORS.split(cleaned)

    items: list[str] = []
    for part in parts:
        item = _TRAILING_JUNK.sub("", part).strip()
        item = item.strip("　 ")
        if not item:
            continue
        if item in _STOP_WORDS:
            continue
        if len(item) > 24:  # 太长的多半是没被拆干净的句子
            continue
        if item in items:
            continue
        items.append(item)
    return items


def normalise_ingredient(name: str | None) -> str:
    """统一大小写、去掉空格和标点，便于比较。"""
    text = str(name or "").strip().lower()
    text = re.sub(r"[\s_\-/·・]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def find_inconvenient_matches(
    recommended_names: Iterable[str],
    inconvenient: Sequence[str],
    *,
    cutoff: float = 0.85,
) -> list[tuple[str, str]]:
    """找出推荐里「用户明确不方便买」的食材。

    返回 ``[(推荐里的名字, 用户写的那一项), ...]``。这是一个确定性的本地检查，
    用来兜住 AI 偶尔不听话的情况（比如仍然写了牛油果）。
    """
    hard = [(name, normalise_ingredient(name)) for name in inconvenient if name]
    hard = [(raw, key) for raw, key in hard if key]
    matches: list[tuple[str, str]] = []
    for name in recommended_names:
        key = normalise_ingredient(name)
        if not key:
            continue
        for raw, hard_key in hard:
            same = key == hard_key
            contained = len(hard_key) >= 2 and (hard_key in key or key in hard_key)
            similar = difflib.SequenceMatcher(None, key, hard_key).ratio() >= cutoff
            if same or contained or similar:
                matches.append((name, raw))
                break
    return matches


def country_label(code: str) -> str:
    for value, label in COUNTRIES:
        if value == code:
            return label
    return code or tr("purchase.country_other")


def budget_label(code: str) -> str:
    for value, label in BUDGETS:
        if value == code:
            return label
    return tr("purchase.budget_normal")


def difficulty_label(code: str) -> str:
    """easy / moderate / hard → 中文。"""
    key = str(code or "").strip().lower()
    if key in {"easy", "容易", "简单"}:
        return tr("purchase.difficulty_easy")
    if key in {"hard", "difficult", "难", "不容易"}:
        return tr("purchase.difficulty_hard")
    if key in {"moderate", "medium", "一般", "中等"}:
        return tr("purchase.difficulty_moderate")
    return ""
