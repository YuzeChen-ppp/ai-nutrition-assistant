"""SQLite storage.

One file (``data/nutrition.db`` in development, ``%LOCALAPPDATA%`` when
packaged) holds the profile, the meals and the food items. Everything is
written with parameterised SQL, and all totals are calculated with SQL or with
the pure functions in :mod:`app.nutrition.calculator` - never by the AI.
"""

from __future__ import annotations

import sqlite3
import threading
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from app.database.models import (
    DailySummary,
    FoodItemRecord,
    MealRecord,
    PurchasePreferences,
    UserProfile,
)
from app.i18n import tr
from app.nutrition.calculator import DailyTotals, sum_food_items
from app.nutrition.targets import NutritionTargets
from app.utils.logging_setup import get_logger
from app.utils.validation import normalise_meal_type

__all__ = ["Database", "DatabaseError", "SCHEMA_VERSION"]

_logger = get_logger("database")

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profile (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    age               INTEGER NOT NULL,
    sex               TEXT    NOT NULL,
    height_cm         REAL    NOT NULL,
    weight_kg         REAL    NOT NULL,
    activity_level    TEXT    NOT NULL,
    goal              TEXT    NOT NULL,
    bmr               REAL,
    tdee              REAL,
    calorie_target    REAL,
    protein_target_g  REAL,
    carbs_target_g    REAL,
    fat_target_g      REAL,
    fibre_target_g    REAL,
    sugar_limit_g     REAL,
    sodium_limit_mg   REAL,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    meal_type   TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id             INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    amount              REAL,
    unit                TEXT,
    estimated_weight_g  REAL,
    calories_kcal       REAL NOT NULL DEFAULT 0,
    protein_g           REAL NOT NULL DEFAULT 0,
    carbs_g             REAL NOT NULL DEFAULT 0,
    fat_g               REAL NOT NULL DEFAULT 0,
    sugar_g             REAL NOT NULL DEFAULT 0,
    fibre_g             REAL NOT NULL DEFAULT 0,
    sodium_mg           REAL NOT NULL DEFAULT 0,
    confidence          REAL,
    cooking_method      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
CREATE INDEX IF NOT EXISTS idx_food_items_meal ON food_items(meal_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_preferences (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    country              TEXT NOT NULL DEFAULT '',
    stores               TEXT NOT NULL DEFAULT '[]',
    easy_ingredients     TEXT NOT NULL DEFAULT '',
    hard_ingredients     TEXT NOT NULL DEFAULT '',
    budget               TEXT NOT NULL DEFAULT 'normal',
    equipment            TEXT NOT NULL DEFAULT '[]',
    max_cooking_minutes  INTEGER NOT NULL DEFAULT 0,
    diet_preferences     TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS daily_summaries AS
SELECT
    m.date                                   AS date,
    COUNT(DISTINCT m.id)                     AS meal_count,
    COALESCE(SUM(f.calories_kcal), 0)        AS calories_kcal,
    COALESCE(SUM(f.protein_g), 0)            AS protein_g,
    COALESCE(SUM(f.carbs_g), 0)              AS carbs_g,
    COALESCE(SUM(f.fat_g), 0)                AS fat_g,
    COALESCE(SUM(f.sugar_g), 0)              AS sugar_g,
    COALESCE(SUM(f.fibre_g), 0)              AS fibre_g,
    COALESCE(SUM(f.sodium_mg), 0)            AS sodium_mg
FROM meals AS m
LEFT JOIN food_items AS f ON f.meal_id = m.id
GROUP BY m.date;
"""


class DatabaseError(Exception):
    """Raised for any storage problem. ``user_message`` is safe for the GUI."""

    message_key = "error.database"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.user_message = message or tr(self.message_key)
        super().__init__(self.user_message)
        self.detail = detail


class Database:
    """Small SQLite wrapper. One instance per app, shared by all pages."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                self._connection = connection
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc
        return self._connection

    def initialize(self) -> None:
        """Create the schema when the database file is new."""
        with self._lock:
            connection = self.connect()
            try:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc
        _logger.info("数据库已就绪：%s", self.path)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:  # pragma: no cover - closing best effort
                    pass
                self._connection = None

    def __enter__(self) -> "Database":
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --------------------------------------------------------------- profile
    def save_profile(self, profile: UserProfile, targets: NutritionTargets | None = None) -> None:
        stored = targets or profile.calculate_targets()
        now = _now()
        sql = """
        INSERT INTO user_profile (
            id, age, sex, height_cm, weight_kg, activity_level, goal,
            bmr, tdee, calorie_target, protein_target_g, carbs_target_g,
            fat_target_g, fibre_target_g, sugar_limit_g, sodium_limit_mg,
            created_at, updated_at
        ) VALUES (
            1, :age, :sex, :height_cm, :weight_kg, :activity_level, :goal,
            :bmr, :tdee, :calorie_target, :protein_target_g, :carbs_target_g,
            :fat_target_g, :fibre_target_g, :sugar_limit_g, :sodium_limit_mg,
            :created_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            age = excluded.age,
            sex = excluded.sex,
            height_cm = excluded.height_cm,
            weight_kg = excluded.weight_kg,
            activity_level = excluded.activity_level,
            goal = excluded.goal,
            bmr = excluded.bmr,
            tdee = excluded.tdee,
            calorie_target = excluded.calorie_target,
            protein_target_g = excluded.protein_target_g,
            carbs_target_g = excluded.carbs_target_g,
            fat_target_g = excluded.fat_target_g,
            fibre_target_g = excluded.fibre_target_g,
            sugar_limit_g = excluded.sugar_limit_g,
            sodium_limit_mg = excluded.sodium_limit_mg,
            updated_at = excluded.updated_at
        """
        params = {
            "age": profile.age,
            "sex": profile.sex,
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "activity_level": profile.activity_level,
            "goal": profile.goal,
            "bmr": stored.bmr,
            "tdee": stored.tdee,
            "calorie_target": stored.calorie_target,
            "protein_target_g": stored.protein_g,
            "carbs_target_g": stored.carbs_g,
            "fat_target_g": stored.fat_g,
            "fibre_target_g": stored.fibre_g,
            "sugar_limit_g": stored.sugar_limit_g,
            "sodium_limit_mg": stored.sodium_limit_mg,
            "created_at": now,
            "updated_at": now,
        }
        self._execute(sql, params)
        _logger.info(
            "已保存个人资料（目标=%s，每日热量目标=%.0f kcal）",
            profile.goal,
            stored.calorie_target,
        )

    def get_profile(self) -> UserProfile | None:
        row = self._query_one("SELECT * FROM user_profile WHERE id = 1")
        if row is None:
            return None
        return UserProfile(
            age=int(row["age"]),
            sex=row["sex"],
            height_cm=float(row["height_cm"]),
            weight_kg=float(row["weight_kg"]),
            activity_level=row["activity_level"],
            goal=row["goal"],
        )

    def get_targets(self) -> NutritionTargets | None:
        row = self._query_one("SELECT * FROM user_profile WHERE id = 1")
        if row is None or row["calorie_target"] is None:
            return None
        return NutritionTargets(
            bmr=float(row["bmr"] or 0.0),
            tdee=float(row["tdee"] or 0.0),
            calorie_target=float(row["calorie_target"] or 0.0),
            protein_g=float(row["protein_target_g"] or 0.0),
            carbs_g=float(row["carbs_target_g"] or 0.0),
            fat_g=float(row["fat_target_g"] or 0.0),
            fibre_g=float(row["fibre_target_g"] or 0.0),
            sugar_limit_g=float(row["sugar_limit_g"] or 0.0),
            sodium_limit_mg=float(row["sodium_limit_mg"] or 0.0),
        )

    # ----------------------------------------------------------------- meals
    def add_meal(
        self,
        items: Sequence[Any],
        *,
        meal_type: str = "snack",
        date: str | None = None,
        time: str | None = None,
        source: str = "manual",
        note: str = "",
    ) -> int:
        """Insert one meal with its food items and return the new meal id."""
        if not items:
            raise DatabaseError(tr("error.meal_needs_items"))
        now = _now()
        meal_date = date or date_cls.today().isoformat()
        meal_time = time or datetime.now().strftime("%H:%M")
        meal_type = normalise_meal_type(meal_type)
        with self._lock:
            connection = self.connect()
            try:
                with connection:  # single transaction
                    cursor = connection.execute(
                        """
                        INSERT INTO meals (date, time, meal_type, source, note, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (meal_date, meal_time, meal_type, source, note, now, now),
                    )
                    meal_id = int(cursor.lastrowid)
                    for item in items:
                        fields = _item_fields(item)
                        fields.update({"meal_id": meal_id, "created_at": now, "updated_at": now})
                        connection.execute(
                            """
                            INSERT INTO food_items (
                                meal_id, name, amount, unit, estimated_weight_g,
                                calories_kcal, protein_g, carbs_g, fat_g, sugar_g,
                                fibre_g, sodium_mg, confidence, cooking_method,
                                created_at, updated_at
                            ) VALUES (
                                :meal_id, :name, :amount, :unit, :estimated_weight_g,
                                :calories_kcal, :protein_g, :carbs_g, :fat_g, :sugar_g,
                                :fibre_g, :sodium_mg, :confidence, :cooking_method,
                                :created_at, :updated_at
                            )
                            """,
                            fields,
                        )
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc
        _logger.info(
            "已保存饮食记录 #%s（%s，%s，%s 项，来源=%s）",
            meal_id,
            meal_date,
            meal_type,
            len(items),
            source,
        )
        return meal_id

    def get_meal(self, meal_id: int) -> MealRecord | None:
        row = self._query_one("SELECT * FROM meals WHERE id = ?", (meal_id,))
        if row is None:
            return None
        items = [
            FoodItemRecord.from_row(item_row)
            for item_row in self._query_all(
                "SELECT * FROM food_items WHERE meal_id = ? ORDER BY id", (meal_id,)
            )
        ]
        return MealRecord.from_row(row, items)

    def meals_for_date(self, day: str) -> list[MealRecord]:
        rows = self._query_all(
            "SELECT * FROM meals WHERE date = ? ORDER BY time, id", (day,)
        )
        return [self._meal_with_items(row) for row in rows]

    def meals_between(self, start: str, end: str) -> list[MealRecord]:
        rows = self._query_all(
            "SELECT * FROM meals WHERE date BETWEEN ? AND ? ORDER BY date, time, id",
            (start, end),
        )
        return [self._meal_with_items(row) for row in rows]

    def recent_days(self, limit: int = 90) -> list[DailySummary]:
        rows = self._query_all(
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT ?", (int(limit),)
        )
        return [_summary_from_row(row) for row in rows]

    def daily_totals(self, day: str) -> DailyTotals:
        row = self._query_one("SELECT * FROM daily_summaries WHERE date = ?", (day,))
        if row is None:
            return DailyTotals()
        return _totals_from_row(row)

    def meal_totals(self, meal_id: int) -> DailyTotals:
        rows = self._query_all(
            "SELECT * FROM food_items WHERE meal_id = ?", (meal_id,)
        )
        return sum_food_items([dict(row) for row in rows])

    def count_meals(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS n FROM meals")
        return int(row["n"]) if row else 0

    def all_dates(self) -> list[str]:
        rows = self._query_all("SELECT DISTINCT date FROM meals ORDER BY date DESC")
        return [str(row["date"]) for row in rows]

    def update_meal(self, meal_id: int, **fields: Any) -> None:
        """Update meal columns (``meal_type``, ``date``, ``time``, ``note``)."""
        allowed = {"date", "time", "meal_type", "note", "source"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "meal_type" in updates:
            updates["meal_type"] = normalise_meal_type(updates["meal_type"])
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        self._execute(
            f"UPDATE meals SET {assignments} WHERE id = :meal_id",
            {**updates, "meal_id": meal_id},
        )

    def delete_meal(self, meal_id: int) -> None:
        """Delete a meal and (via ``ON DELETE CASCADE``) all of its food items."""
        self._execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        _logger.info("已删除饮食记录 #%s", meal_id)

    # ------------------------------------------------------------ food items
    def add_food_item(self, meal_id: int, item: Any) -> int:
        now = _now()
        fields = _item_fields(item)
        fields.update({"meal_id": meal_id, "created_at": now, "updated_at": now})
        with self._lock:
            connection = self.connect()
            try:
                with connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO food_items (
                            meal_id, name, amount, unit, estimated_weight_g,
                            calories_kcal, protein_g, carbs_g, fat_g, sugar_g,
                            fibre_g, sodium_mg, confidence, cooking_method,
                            created_at, updated_at
                        ) VALUES (
                            :meal_id, :name, :amount, :unit, :estimated_weight_g,
                            :calories_kcal, :protein_g, :carbs_g, :fat_g, :sugar_g,
                            :fibre_g, :sodium_mg, :confidence, :cooking_method,
                            :created_at, :updated_at
                        )
                        """,
                        fields,
                    )
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc
        return int(cursor.lastrowid)

    def get_food_item(self, item_id: int) -> FoodItemRecord | None:
        row = self._query_one("SELECT * FROM food_items WHERE id = ?", (item_id,))
        return FoodItemRecord.from_row(row) if row else None

    def food_items_for_meal(self, meal_id: int) -> list[FoodItemRecord]:
        return [
            FoodItemRecord.from_row(row)
            for row in self._query_all(
                "SELECT * FROM food_items WHERE meal_id = ? ORDER BY id", (meal_id,)
            )
        ]

    def update_food_item(self, item_id: int, **fields: Any) -> None:
        allowed = {
            "name",
            "amount",
            "unit",
            "estimated_weight_g",
            "calories_kcal",
            "protein_g",
            "carbs_g",
            "fat_g",
            "sugar_g",
            "fibre_g",
            "sodium_mg",
            "confidence",
            "cooking_method",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        self._execute(
            f"UPDATE food_items SET {assignments} WHERE id = :item_id",
            {**updates, "item_id": item_id},
        )

    def delete_food_item(self, item_id: int) -> None:
        self._execute("DELETE FROM food_items WHERE id = ?", (item_id,))

    # -------------------------------------------------------- 应用设置（键值对）
    def get_app_setting(self, key: str, default: str | None = None) -> str | None:
        """读取界面设置（例如语言、是否跳过 API Key 引导）。"""
        row = self._query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        if row is None:
            return default
        return str(row["value"])

    def set_app_setting(self, key: str, value: str) -> None:
        self._execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, str(value), _now()),
        )

    # ------------------------------------------------------------ 购买条件
    def get_purchase_preferences(self) -> PurchasePreferences:
        """读取「我方便买到什么」的设置（没有则返回空设置）。"""
        row = self._query_one("SELECT * FROM purchase_preferences WHERE id = 1")
        if row is None:
            return PurchasePreferences()
        data = dict(row)
        return PurchasePreferences(
            country=str(data.get("country") or ""),
            stores=_load_json_list(data.get("stores")),
            easy_text=str(data.get("easy_ingredients") or ""),
            hard_text=str(data.get("hard_ingredients") or ""),
            budget=str(data.get("budget") or "normal"),
            equipment=_load_json_list(data.get("equipment")),
            max_cooking_minutes=int(data.get("max_cooking_minutes") or 0),
            diet_preferences=str(data.get("diet_preferences") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def save_purchase_preferences(self, preferences: PurchasePreferences) -> None:
        """保存购买条件（推荐下一餐时会带上这些信息）。"""
        self._execute(
            """
            INSERT INTO purchase_preferences (
                id, country, stores, easy_ingredients, hard_ingredients, budget,
                equipment, max_cooking_minutes, diet_preferences, updated_at
            ) VALUES (
                1, :country, :stores, :easy, :hard, :budget,
                :equipment, :minutes, :diet, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                country = excluded.country,
                stores = excluded.stores,
                easy_ingredients = excluded.easy_ingredients,
                hard_ingredients = excluded.hard_ingredients,
                budget = excluded.budget,
                equipment = excluded.equipment,
                max_cooking_minutes = excluded.max_cooking_minutes,
                diet_preferences = excluded.diet_preferences,
                updated_at = excluded.updated_at
            """,
            {
                "country": preferences.country,
                "stores": json.dumps(list(preferences.stores), ensure_ascii=False),
                "easy": preferences.easy_text,
                "hard": preferences.hard_text,
                "budget": preferences.budget,
                "equipment": json.dumps(list(preferences.equipment), ensure_ascii=False),
                "minutes": int(preferences.max_cooking_minutes or 0),
                "diet": preferences.diet_preferences,
                "updated_at": _now(),
            },
        )
        _logger.info(
            "已保存购买条件（国家=%s，常去超市=%s，方便购买=%s 项，不方便=%s 项）",
            preferences.country or "-",
            "、".join(preferences.stores) or "-",
            len(preferences.easy_ingredients()),
            len(preferences.hard_ingredients()),
        )

    # ------------------------------------------------------------- internals
    def _meal_with_items(self, row: sqlite3.Row) -> MealRecord:
        items = self.food_items_for_meal(int(row["id"]))
        return MealRecord.from_row(row, items)

    def _execute(self, sql: str, params: Mapping[str, Any] | Sequence[Any]) -> None:
        with self._lock:
            connection = self.connect()
            try:
                with connection:
                    connection.execute(sql, params)
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc

    def _query_one(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> sqlite3.Row | None:
        with self._lock:
            connection = self.connect()
            try:
                cursor = connection.execute(sql, tuple(params))
                return cursor.fetchone()
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc

    def _query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            connection = self.connect()
            try:
                cursor = connection.execute(sql, tuple(params))
                return list(cursor.fetchall())
            except sqlite3.Error as exc:
                raise DatabaseError(detail=str(exc)) from exc


def _item_fields(item: Any) -> dict[str, Any]:
    """Normalise a Pydantic model / dataclass / dict into insert parameters."""
    if hasattr(item, "to_record_fields"):
        data = dict(item.to_record_fields())
    elif isinstance(item, Mapping):
        data = dict(item)
    else:
        data = {
            field: getattr(item, field, None)
            for field in (
                "name",
                "amount",
                "unit",
                "estimated_weight_g",
                "calories_kcal",
                "protein_g",
                "carbs_g",
                "fat_g",
                "sugar_g",
                "fibre_g",
                "sodium_mg",
                "confidence",
                "cooking_method",
            )
        }
    name = str(data.get("name") or "").strip()
    if not name:
        raise DatabaseError(tr("error.food_name_required"))
    return {
        "name": name[:120],
        "amount": data.get("amount"),
        "unit": data.get("unit"),
        "estimated_weight_g": data.get("estimated_weight_g"),
        "calories_kcal": float(data.get("calories_kcal") or 0.0),
        "protein_g": float(data.get("protein_g") or 0.0),
        "carbs_g": float(data.get("carbs_g") or 0.0),
        "fat_g": float(data.get("fat_g") or 0.0),
        "sugar_g": float(data.get("sugar_g") or 0.0),
        "fibre_g": float(data.get("fibre_g") or 0.0),
        "sodium_mg": float(data.get("sodium_mg") or 0.0),
        "confidence": data.get("confidence"),
        "cooking_method": data.get("cooking_method"),
    }


def _totals_from_row(row: sqlite3.Row) -> DailyTotals:
    data = dict(row)
    return DailyTotals(
        calories_kcal=float(data.get("calories_kcal") or 0.0),
        protein_g=float(data.get("protein_g") or 0.0),
        carbs_g=float(data.get("carbs_g") or 0.0),
        fat_g=float(data.get("fat_g") or 0.0),
        sugar_g=float(data.get("sugar_g") or 0.0),
        fibre_g=float(data.get("fibre_g") or 0.0),
        sodium_mg=float(data.get("sodium_mg") or 0.0),
    )


def _summary_from_row(row: sqlite3.Row) -> DailySummary:
    data = dict(row)
    return DailySummary(
        date=str(data["date"]),
        totals=_totals_from_row(row),
        meal_count=int(data.get("meal_count") or 0),
    )


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _load_json_list(value: Any) -> list[str]:
    """读取以 JSON 数组形式保存的列表（旧数据/损坏数据都不会让程序报错）。"""
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return [part.strip() for part in str(value).split(",") if part.strip()]
    if isinstance(loaded, list):
        return [str(item) for item in loaded]
    return []
