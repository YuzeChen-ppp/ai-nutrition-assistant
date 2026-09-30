"""AI Nutrition Assistant - application entry point.

Normal use:  python main.py
Helpers:     python main.py --smoke-test     (start the GUI headless and exit)
             python main.py --check-api      (verify the DeepSeek API key)
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from app import __version__
from app.context import AppContext
from app.utils.config import APP_NAME, data_dir, db_path, log_dir
from app.utils.logging_setup import get_logger, setup_logging


def _use_utf8_console() -> None:
    """让命令行里的中文尽量正常显示（不影响程序界面）。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:  # --windowed 的 EXE 没有控制台
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 某些终端不支持
            pass


def _say(text: str = "") -> None:
    """安全地输出一行文本。

    ``--windowed`` 打包的 EXE 在没有控制台时 ``sys.stdout`` 为 ``None``，
    此时直接 ``print`` 会抛异常；这种情况下改为写进日志文件。
    """
    stream = sys.stdout
    if stream is not None:
        try:
            print(text, file=stream, flush=True)
            return
        except (OSError, ValueError, AttributeError):  # pragma: no cover - 防御
            pass
    get_logger("main").info(text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="AI Nutrition Assistant",
        description="用 DeepSeek V4.1 Flash 分析食物，并记录每日营养（AI 饮食助手）。",
    )
    parser.add_argument("--db", type=Path, default=None, help="Use a specific SQLite file.")
    parser.add_argument("--debug", action="store_true", help="输出更详细的日志。")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="离屏启动界面后退出（用于自动测试）。",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="离线自检（界面 + 数据库 + 计算），不联网。",
    )
    parser.add_argument(
        "--check-api",
        action="store_true",
        help="调用 DeepSeek API 测试 API Key 并退出。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _use_utf8_console()
    args = parse_args(argv)

    if args.smoke_test or args.self_test:
        # Must be set before QApplication is created.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.self_test and args.db is None:
        args.db = Path(tempfile.mkdtemp(prefix="nutrition-self-test-")) / "self-test.db"

    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow
    from app.ui.styles import apply_app_style

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    apply_app_style(app)

    logger = get_logger("main")
    try:
        context = AppContext.create(database_path=args.db)
    except Exception as exc:  # noqa: BLE001 - startup must never crash silently
        setup_logging(log_dir(), debug=args.debug)
        logger.exception("Startup failed")
        _fatal(
            "程序无法打开本地数据库。\n\n" + _startup_details(exc),
            gui=not (args.self_test or args.smoke_test or args.check_api),
        )
        return 1

    setup_logging(log_dir(), debug=args.debug or context.settings.debug)
    logger.info("启动 %s %s", APP_NAME, __version__)
    logger.info("数据库：%s", context.database.path)

    if args.check_api:
        return _check_api(context)

    if args.self_test:
        return _self_test(context)

    window = MainWindow(context)
    window.show()

    if args.smoke_test:
        app.processEvents()
        profile = context.database.get_profile()
        today = context.database.count_meals()
        _say("界面启动自检通过（SMOKE OK）")
        _say(f"  窗口标题     : {window.windowTitle()}")
        _say(f"  当前页面     : {window.stack.currentWidget().__class__.__name__}")
        _say(f"  是否已设置资料: {bool(profile)}")
        _say(f"  已保存餐数   : {today}")
        _say(f"  数据库文件   : {context.database.path}")
        _say(f"  数据目录     : {data_dir()}")
        _say(f"  是否已有 API Key: {context.settings.has_api_key}")
        window.close()
        return 0

    return app.exec()


def _check_api(context: AppContext) -> int:
    if not context.settings.has_api_key:
        _say("尚未设置 DeepSeek API Key。")
        _say("请在程序界面「设置 → AI 设置」中输入，或者手动创建一个 .env 文件，内容为：")
        _say("    DEEPSEEK_API_KEY=你的密钥")
        return 2
    _say(f"模型：{context.settings.model}    接口地址：{context.settings.base_url}")
    try:
        message = context.client().check_connection()
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        from app.ai.deepseek_client import describe_api_error

        _say(f"测试失败：{describe_api_error(exc)}")
        return 1
    _say(message)
    return 0


def _self_test(context: AppContext) -> int:
    """Offline end-to-end check: imports, GUI, database and arithmetic."""
    import importlib
    from datetime import date

    from app.ai.schemas import FoodItem
    from app.database.models import UserProfile
    from app.ui.main_window import MainWindow

    failures: list[str] = []

    for module in ("PySide6.QtWidgets", "pydantic", "PIL.Image", "openai", "dotenv"):
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - reported below
            failures.append(f"import {module}: {exc}")

    window = MainWindow(context)
    window.save_profile(
        UserProfile(
            age=30, sex="male", height_cm=180, weight_kg=80,
            activity_level="moderate", goal="maintain",
        )
    )
    targets = context.database.get_targets()
    if targets is None or targets.calorie_target <= 0:
        failures.append("profile targets were not stored")

    items = [
        FoodItem(name="Food A", calories_kcal=500, protein_g=30, carbs_g=50, fat_g=20),
        FoodItem(name="Food B", calories_kcal=700, protein_g=40, carbs_g=70, fat_g=25),
        FoodItem(name="Food C", calories_kcal=300, protein_g=10, carbs_g=30, fat_g=15),
    ]
    meal_id = window.save_items(items, meal_type="lunch", source="manual")
    if meal_id is None:
        failures.append("the meal could not be saved")

    today = date.today().isoformat()
    totals = context.database.daily_totals(today)
    expected = {
        "calories": (totals.calories_kcal, 1500.0),
        "protein": (totals.protein_g, 80.0),
        "carbs": (totals.carbs_g, 150.0),
        "fat": (totals.fat_g, 60.0),
    }
    for name, (actual, wanted) in expected.items():
        if abs(actual - wanted) > 0.01:
            failures.append(f"{name}: expected {wanted}, got {actual}")

    if "1,500" not in window.dashboard.consumed_value.text():
        failures.append(
            f"dashboard shows {window.dashboard.consumed_value.text()!r} instead of 1,500"
        )

    # 数值解析回归：界面把钠显示成 "4,200"（千位分隔符），读回来必须还是 4200，
    # 否则合计会变成 4.2 + 180 = 184（曾经真实出现过的 bug）。
    from app.utils.validation import coerce_number

    sodium_a = coerce_number("4,200")
    sodium_b = coerce_number("180")
    if sodium_a != 4200.0:
        failures.append(f"'4,200' 被解析成 {sodium_a}（应为 4200）")
    if sodium_b != 180.0:
        failures.append(f"'180' 被解析成 {sodium_b}（应为 180）")
    if (sodium_a or 0) + (sodium_b or 0) != 4380.0:
        failures.append("钠合计检查失败")
    if coerce_number("0,5") != 0.5:
        failures.append("欧洲小数逗号 '0,5' 解析失败")

    context.database.close()
    reopened = context.database.connect()
    persisted = reopened.execute("SELECT COUNT(*) FROM meals").fetchone()[0]
    if persisted != 1:
        failures.append(f"data did not persist: {persisted} meal(s) after reopening")

    window.close()

    if failures:
        _say("自检失败（SELF TEST FAILED）")
        for failure in failures:
            _say(f"  - {failure}")
        return 1

    _say("自检通过（SELF TEST OK）")
    _say(f"  数据库文件   : {context.database.path}")
    _say(f"  当日热量合计 : {totals.calories_kcal:,.0f} kcal")
    _say(f"  当日蛋白质   : {totals.protein_g:,.1f} g")
    _say(f"  每日热量目标 : {targets.calorie_target:,.0f} kcal")
    _say(f"  主界面显示   : 已摄入 {window.dashboard.consumed_value.text()} kcal")
    _say(f"  数值解析检查 : 4,200 + 180 = {(sodium_a or 0) + (sodium_b or 0):,.0f} mg（应为 4,380）")
    return 0


def _fatal(message: str, *, gui: bool = True) -> None:
    """报告启动失败：写日志 + 控制台输出，必要时再弹一个对话框。"""
    _say(message)
    get_logger("main").error(message)
    if not gui:
        return
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # 自动测试 / 自检模式：没有人能点击对话框，弹出只会「假死」。
        return
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv[:1])
        _ = app
        QMessageBox.critical(None, f"{APP_NAME} - 启动失败", message)
    except Exception:  # noqa: BLE001 - 控制台/日志输出已经足够
        pass


def _startup_details(exc: BaseException) -> str:
    """Describe a startup failure without ever raising a second error."""
    lines = [str(exc) or exc.__class__.__name__]
    try:
        lines.append(f"Expected database location: {db_path()}")
    except Exception:  # noqa: BLE001 - a broken path must not mask the original
        pass
    try:
        lines.append(f"Data folder: {data_dir()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        lines.append(f"Log folder: {log_dir()}")
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(lines)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - last line of defence
        setup_logging(log_dir(), debug=False)
        get_logger("main").exception("Unhandled error while starting the app")
        _fatal("程序无法启动。\n\n" + _startup_details(error))
        raise SystemExit(1) from None
