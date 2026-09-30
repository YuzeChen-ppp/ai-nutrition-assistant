"""Database insert / retrieve / update / delete and persistence."""

from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.database.database import Database, DatabaseError
from app.database.models import UserProfile


def food(name: str, kcal: float, protein: float = 0, carbs: float = 0, fat: float = 0) -> dict:
    return {
        "name": name,
        "estimated_weight_g": 100,
        "calories_kcal": kcal,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
    }


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "test.db"
        self.db = Database(self.path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()


class MealTests(DatabaseTestCase):
    def test_insert_and_retrieve_meal(self) -> None:
        meal_id = self.db.add_meal(
            [food("Chicken breast", 180, 30, 0, 4), food("Rice", 200, 4, 44, 0.4)],
            meal_type="lunch",
            date="2026-09-14",
            time="12:30",
            source="manual",
        )
        meal = self.db.get_meal(meal_id)
        self.assertIsNotNone(meal)
        assert meal is not None
        self.assertEqual(meal.meal_type, "lunch")
        self.assertEqual(meal.date, "2026-09-14")
        self.assertEqual(len(meal.items), 2)
        self.assertAlmostEqual(meal.totals().calories_kcal, 380.0)

    def test_unknown_meal_returns_none(self) -> None:
        self.assertIsNone(self.db.get_meal(999))

    def test_meal_without_items_is_rejected(self) -> None:
        with self.assertRaises(DatabaseError):
            self.db.add_meal([], meal_type="dinner")

    def test_food_item_without_name_is_rejected(self) -> None:
        with self.assertRaises(DatabaseError):
            self.db.add_meal([{"name": "  ", "calories_kcal": 10}])

    def test_meals_for_date(self) -> None:
        self.db.add_meal([food("Oats", 150)], meal_type="breakfast", date="2026-09-14")
        self.db.add_meal([food("Salad", 90)], meal_type="lunch", date="2026-09-14")
        self.db.add_meal([food("Soup", 60)], meal_type="dinner", date="2026-09-13")
        self.assertEqual(len(self.db.meals_for_date("2026-09-14")), 2)
        self.assertEqual(len(self.db.meals_for_date("2026-09-13")), 1)
        self.assertEqual(self.db.count_meals(), 3)
        self.assertEqual(self.db.all_dates(), ["2026-09-14", "2026-09-13"])


class TotalsTests(DatabaseTestCase):
    def test_daily_totals_use_sql_aggregation(self) -> None:
        self.db.add_meal(
            [food("A", 500, 30, 50, 20), food("B", 700, 40, 70, 25)],
            date="2026-09-14",
        )
        self.db.add_meal([food("C", 300, 10, 30, 15)], date="2026-09-14")
        totals = self.db.daily_totals("2026-09-14")
        self.assertAlmostEqual(totals.calories_kcal, 1500.0)
        self.assertAlmostEqual(totals.protein_g, 80.0)
        self.assertAlmostEqual(totals.carbs_g, 150.0)
        self.assertAlmostEqual(totals.fat_g, 60.0)

    def test_empty_day_is_zero(self) -> None:
        totals = self.db.daily_totals("2000-01-01")
        self.assertEqual(totals.calories_kcal, 0.0)

    def test_recent_days_summary(self) -> None:
        self.db.add_meal([food("A", 800, 40)], date="2026-09-14")
        self.db.add_meal([food("B", 900, 50)], date="2026-09-13")
        days = self.db.recent_days(limit=10)
        self.assertEqual([day.date for day in days], ["2026-09-14", "2026-09-13"])
        self.assertAlmostEqual(days[0].totals.calories_kcal, 800.0)


class UpdateDeleteTests(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.meal_id = self.db.add_meal(
            [food("Chicken", 200, 40), food("Rice", 250, 5, 55, 1)],
            meal_type="dinner",
            date="2026-09-14",
        )

    def test_update_food_item(self) -> None:
        items = self.db.food_items_for_meal(self.meal_id)
        self.db.update_food_item(items[0].id, calories_kcal=165, name="Chicken breast")
        updated = self.db.get_food_item(items[0].id)
        assert updated is not None
        self.assertEqual(updated.name, "Chicken breast")
        self.assertAlmostEqual(updated.calories_kcal, 165.0)
        self.assertAlmostEqual(self.db.meal_totals(self.meal_id).calories_kcal, 415.0)

    def test_delete_food_item_recalculates_totals(self) -> None:
        items = self.db.food_items_for_meal(self.meal_id)
        self.db.delete_food_item(items[0].id)
        self.assertEqual(len(self.db.food_items_for_meal(self.meal_id)), 1)
        self.assertAlmostEqual(self.db.meal_totals(self.meal_id).calories_kcal, 250.0)

    def test_update_meal_type(self) -> None:
        self.db.update_meal(self.meal_id, meal_type="Lunch", note="moved")
        meal = self.db.get_meal(self.meal_id)
        assert meal is not None
        self.assertEqual(meal.meal_type, "lunch")
        self.assertEqual(meal.note, "moved")

    def test_delete_meal_cascades_to_food_items(self) -> None:
        self.db.delete_meal(self.meal_id)
        self.assertIsNone(self.db.get_meal(self.meal_id))
        self.assertEqual(self.db.food_items_for_meal(self.meal_id), [])
        self.assertEqual(self.db.daily_totals("2026-09-14").calories_kcal, 0.0)


class ProfileTests(DatabaseTestCase):
    def test_profile_round_trip(self) -> None:
        profile = UserProfile(
            age=25, sex="male", height_cm=175, weight_kg=72,
            activity_level="moderate", goal="maintain",
        )
        self.db.save_profile(profile)
        stored = self.db.get_profile()
        self.assertEqual(stored, profile)
        targets = self.db.get_targets()
        assert targets is not None
        self.assertGreater(targets.calorie_target, 1000)

    def test_profile_is_updated_not_duplicated(self) -> None:
        self.db.save_profile(UserProfile(age=25, sex="male", height_cm=175, weight_kg=72))
        self.db.save_profile(UserProfile(age=26, sex="male", height_cm=175, weight_kg=70))
        row_count = self.db._query_one("SELECT COUNT(*) AS n FROM user_profile")
        assert row_count is not None
        self.assertEqual(int(row_count["n"]), 1)
        stored = self.db.get_profile()
        assert stored is not None
        self.assertEqual(stored.age, 26)
        self.assertAlmostEqual(stored.weight_kg, 70.0)

    def test_missing_profile_returns_none(self) -> None:
        self.assertIsNone(self.db.get_profile())
        self.assertIsNone(self.db.get_targets())


class PersistenceTests(unittest.TestCase):
    def test_data_survives_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "persist.db"
            first = Database(path)
            first.initialize()
            first.save_profile(UserProfile(age=30, sex="female", height_cm=165, weight_kg=60))
            first.add_meal([food("Yogurt", 120, 10)], meal_type="breakfast", date="2026-09-14")
            first.close()

            second = Database(path)
            second.initialize()
            self.assertEqual(second.count_meals(), 1)
            self.assertAlmostEqual(second.daily_totals("2026-09-14").calories_kcal, 120.0)
            self.assertIsNotNone(second.get_profile())
            second.close()


class MigrationTests(unittest.TestCase):
    """旧版本（user_version = 1，没有 app_settings 表）的数据库要能直接升级。"""

    OLD_SCHEMA = """
    CREATE TABLE user_profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        age INTEGER NOT NULL, sex TEXT NOT NULL, height_cm REAL NOT NULL,
        weight_kg REAL NOT NULL, activity_level TEXT NOT NULL, goal TEXT NOT NULL,
        bmr REAL, tdee REAL, calorie_target REAL, protein_target_g REAL,
        carbs_target_g REAL, fat_target_g REAL, fibre_target_g REAL,
        sugar_limit_g REAL, sodium_limit_mg REAL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, time TEXT NOT NULL,
        meal_type TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE food_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
        name TEXT NOT NULL, amount REAL, unit TEXT, estimated_weight_g REAL,
        calories_kcal REAL NOT NULL DEFAULT 0, protein_g REAL NOT NULL DEFAULT 0,
        carbs_g REAL NOT NULL DEFAULT 0, fat_g REAL NOT NULL DEFAULT 0,
        sugar_g REAL NOT NULL DEFAULT 0, fibre_g REAL NOT NULL DEFAULT 0,
        sodium_mg REAL NOT NULL DEFAULT 0, confidence REAL, cooking_method TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """

    def test_old_database_gains_the_new_table_and_keeps_its_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(self.OLD_SCHEMA)
                connection.execute("PRAGMA user_version = 1")
                connection.execute(
                    """
                    INSERT INTO user_profile (
                        id, age, sex, height_cm, weight_kg, activity_level, goal,
                        bmr, tdee, calorie_target, protein_target_g, carbs_target_g,
                        fat_target_g, fibre_target_g, sugar_limit_g, sodium_limit_mg,
                        created_at, updated_at
                    ) VALUES (1, 19, 'male', 178, 111, 'sedentary', 'lose',
                              2000, 2400, 2122, 130, 200, 60, 30, 50, 2300,
                              '2026-09-14 16:44:51', '2026-09-14 16:44:51')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO meals (date, time, meal_type, source, note, created_at, updated_at)
                    VALUES ('2026-09-13', '12:00', 'lunch', 'manual', '', 'x', 'x')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            database = Database(path)
            database.initialize()
            # 新版本会补上 app_settings 与 purchase_preferences 两张表
            tables = [
                row["name"]
                for row in database._query_all(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            self.assertIn("app_settings", tables)
            self.assertIn("purchase_preferences", tables)
            profile = database.get_profile()
            assert profile is not None
            self.assertEqual(profile.age, 19)
            self.assertEqual(profile.goal, "lose")
            targets = database.get_targets()
            assert targets is not None
            self.assertAlmostEqual(targets.calorie_target, 2122.0)
            self.assertEqual(database.count_meals(), 1)
            # 新表可读写（语言等界面设置）
            self.assertIsNone(database.get_app_setting("language"))
            database.set_app_setting("language", "zh_CN")
            self.assertEqual(database.get_app_setting("language"), "zh_CN")
            database.close()


if __name__ == "__main__":
    unittest.main()
