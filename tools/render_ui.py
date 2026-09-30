"""把主要界面渲染成 PNG，用于检查中文排版（离屏运行，不需要点鼠标）。

    python tools/render_ui.py            # 输出到 screenshots/
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai.schemas import FoodItem  # noqa: E402
from app.ai.parser import parse_recommendation  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.database.models import UserProfile  # noqa: E402
from app.ui.dialogs import ConfirmFoodDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.utils.config import Settings  # noqa: E402
from app.utils.credentials import ApiKeyStore  # noqa: E402

OUTPUT = ROOT / "screenshots"

# 渲染用的推荐结果（与 tests/fakes.py 里的示例一致）
RECOMMENDATION_PAYLOAD = """
{"title":"鸡胸肉土豆西兰花",
 "foods":[
   {"name":"鸡胸肉","amount":150,"unit":"g","estimated_weight_g":150,
    "calories_kcal":248,"protein_g":46.5,"carbs_g":0,"fat_g":5.4,"sugar_g":0,
    "fibre_g":0,"sodium_mg":111,"cooking_method":"香煎","confidence":0.8},
   {"name":"土豆","estimated_weight_g":200,"calories_kcal":174,
    "protein_g":3.8,"carbs_g":40.2,"fat_g":0.2,"sugar_g":1.8,"fibre_g":3.6,
    "sodium_mg":8,"cooking_method":"水煮","confidence":0.85},
   {"name":"西兰花","estimated_weight_g":150,"calories_kcal":53,
    "protein_g":3.6,"carbs_g":10.8,"fat_g":0.6,"sugar_g":2.1,"fibre_g":5,
    "sodium_mg":62,"cooking_method":"水煮","confidence":0.85}
 ],
 "shopping_list":[
   {"name":"鸡胸肉","quantity":"约 500 g","difficulty":"easy"},
   {"name":"西兰花","quantity":"1 个","difficulty":"easy"},
   {"name":"土豆","quantity":"1 kg","difficulty":"easy"}
 ],
 "difficulty":"easy",
 "reason":"你今天蛋白质摄入偏低，这份餐食主要使用你标记为方便购买的食材，热量也在剩余范围内。",
 "tips":"煎鸡胸肉时少放油，用不粘锅即可。"}
"""


def create_app() -> QApplication:
    """优先使用真实平台（字体齐全），没有桌面时退回离屏渲染。"""
    existing = QApplication.instance()
    if existing is not None:
        return existing
    try:
        return QApplication(sys.argv[:1])
    except Exception:  # noqa: BLE001 - 无桌面环境时退回 offscreen
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        return QApplication(sys.argv[:1])


class _UnavailableCredentialStore:
    """让渲染脚本不去碰真实的 Windows 凭据管理器。"""

    def is_available(self) -> bool:
        return False

    def get(self):  # pragma: no cover - 只在渲染脚本里用
        return None

    def set(self, secret: str) -> None:  # pragma: no cover
        raise RuntimeError("render_ui 不会保存密钥")

    def delete(self) -> None:  # pragma: no cover
        return None


def build_context(workdir: Path, *, with_key: bool) -> AppContext:
    database = Database(workdir / "render.db")
    database.initialize()
    store = ApiKeyStore(
        env_file=workdir / ".env", credential_store=_UnavailableCredentialStore()
    )
    context = AppContext(
        settings=Settings(api_key="sk-render-only-not-real" if with_key else ""),
        database=database,
        api_key_store=store,
    )
    return context


def shot(widget, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    widget.grab().save(str(OUTPUT / f"{name}.png"))
    print(f"wrote {OUTPUT / (name + '.png')}")


def main() -> int:
    app = create_app()
    apply_app_style(app)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        # 1) 首次启动：欢迎页
        context = build_context(workdir, with_key=False)
        window = MainWindow(context)
        window.resize(1180, 860)
        app.processEvents()
        shot(window, "01-welcome")

        # 2) 个人资料页
        context.api_key_store.env_file.set("sk-render-only-not-real")
        context.reload_settings()
        window.refresh_all()
        window._show_page("setup")
        app.processEvents()
        shot(window, "02-profile-setup")

        # 3) 有数据的主界面
        window.save_profile(
            UserProfile(
                age=25, sex="male", height_cm=175, weight_kg=70,
                activity_level="moderate", goal="maintain",
            )
        )
        window.save_items(
            [
                FoodItem(name="鸡蛋", amount=2, unit="个", estimated_weight_g=100,
                         calories_kcal=143, protein_g=12.6, carbs_g=0.7, fat_g=9.5,
                         sugar_g=0.4, fibre_g=0.0, sodium_mg=142, confidence=0.92),
                FoodItem(name="全麦吐司", amount=1, unit="片", estimated_weight_g=35,
                         calories_kcal=86, protein_g=4.6, carbs_g=14.4, fat_g=1.2,
                         sugar_g=2.1, fibre_g=2.5, sodium_mg=140, confidence=0.9),
                FoodItem(name="半脱脂牛奶", amount=1, unit="杯", estimated_weight_g=250,
                         calories_kcal=115, protein_g=8.5, carbs_g=12.0, fat_g=4.0,
                         sugar_g=12.8, fibre_g=0.0, sodium_mg=110, confidence=0.88),
            ],
            meal_type="breakfast",
            source="manual",
        )
        window.save_items(
            [
                FoodItem(name="鸡胸肉", estimated_weight_g=180, calories_kcal=297,
                         protein_g=55.8, carbs_g=0.0, fat_g=6.5, sodium_mg=133,
                         confidence=0.8, cooking_method="香煎"),
                FoodItem(name="米饭", estimated_weight_g=200, calories_kcal=260,
                         protein_g=5.4, carbs_g=56.4, fat_g=0.6, sodium_mg=2,
                         confidence=0.85, cooking_method="蒸"),
                FoodItem(name="西兰花", estimated_weight_g=100, calories_kcal=35,
                         protein_g=2.4, carbs_g=7.2, fat_g=0.4, fibre_g=3.3,
                         sodium_mg=41, confidence=0.85, cooking_method="焯水"),
            ],
            meal_type="lunch",
            source="image",
        )
        window.refresh_all()
        app.processEvents()
        shot(window, "03-dashboard")

        # 4) 设置页
        window.goto("settings")
        form = window.settings.purchase_form
        form.country_combo.setCurrentIndex(form.country_combo.findData("United Kingdom"))
        for name in ("Tesco", "Sainsbury's", "Lidl"):
            form.store_boxes[name].setChecked(True)
        form.easy_edit.setPlainText(
            "我附近超市可以买到鸡胸肉、鸡蛋、牛奶、西兰花、胡萝卜、土豆、米饭和面包。"
        )
        form.hard_edit.setPlainText("牛油果、藜麦、羽衣甘蓝、三文鱼")
        form.budget_combo.setCurrentIndex(form.budget_combo.findData("normal"))
        for name in ("微波炉", "电饭锅", "炒锅 / 平底锅"):
            form.equipment_boxes[name].setChecked(True)
        form.time_spin.setValue(15)
        form.diet_edit.setText("不吃香菜")
        app.processEvents()
        shot(window, "04-settings")

        # 4b) 购买与饮食卡片（单独截一张，方便看清）
        form.updateGeometry()
        app.processEvents()
        form.grab().save(str(OUTPUT / "07-purchase-settings.png"))
        print(f"wrote {OUTPUT / '07-purchase-settings.png'}")

        # 4c) 推荐结果（抓整个窗口，和真实使用时看到的一致）
        window.goto("today")
        recommendation = parse_recommendation(RECOMMENDATION_PAYLOAD)
        window.dashboard.set_recommendation(
            recommendation,
            remaining_kcal=700,
            purchase_context="参考：英国 ・ 常去超市：Tesco、Sainsbury's、Lidl ・ 预算：普通 ・ 烹饪时间：15 分钟",
        )
        window.resize(1180, 1500)
        app.processEvents()
        window.dashboard.recommendation_card.updateGeometry()
        app.processEvents()
        shot(window, "08-recommendation")

        # 4d) 推荐卡片单独一张（adjustSize 之后再抓，避免布局还没算完）
        card = window.dashboard.recommendation_card
        card.adjustSize()
        app.processEvents()
        card.grab().save(str(OUTPUT / "09-recommendation-card.png"))
        print(f"wrote {OUTPUT / '09-recommendation-card.png'}")

        # 5) 历史记录页
        window.goto("history")
        window.on_history_day_selected(window.dashboard.current_date())
        app.processEvents()
        shot(window, "05-history")

        # 6) 确认识别结果对话框
        dialog = ConfirmFoodDialog(
            [
                FoodItem(name="鸡胸肉", estimated_weight_g=180, calories_kcal=297,
                         protein_g=55.8, carbs_g=0.0, fat_g=6.5, sodium_mg=133,
                         confidence=0.62, cooking_method="香煎"),
                FoodItem(name="米饭", estimated_weight_g=200, calories_kcal=260,
                         protein_g=5.4, carbs_g=56.4, fat_g=0.6, sodium_mg=2,
                         confidence=0.85),
                FoodItem(name="西兰花", estimated_weight_g=100, calories_kcal=35,
                         protein_g=2.4, carbs_g=7.2, fat_g=0.4, fibre_g=3.3,
                         sodium_mg=41, confidence=0.85),
            ],
            source="image",
            note="油和酱料的用量是估算值。",
        )
        dialog.resize(1120, 640)
        app.processEvents()
        shot(dialog, "06-confirm")
        dialog.close()

        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
