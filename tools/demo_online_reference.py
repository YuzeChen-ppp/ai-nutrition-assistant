"""Demonstrate the "查询在线参考" button offline, with no API key, and online.

This is an evidence/QA tool, not part of the application. It answers the
question the acceptance list asks: what does the user actually see when the
button is pressed with no network, with no key, and when it works?

Three scenarios, each rendered offscreen to ``screenshots/``:

``offline``  A real :class:`~app.ai.deepseek_client.DeepSeekClient` whose HTTP
             layer is replaced by a stub that raises the same connection error
             the ``openai`` SDK raises when the network is down. Nothing is sent
             anywhere: no socket is opened, no DNS lookup happens.
``no-key``   The same dialog built without an API key, where the button is
             disabled and explains itself.
``live``     Optional (``--live``): the real client, one real request. Only run
             this when you have a key and want to see the online values; it is
             the one path in this tool that touches the network.

Usage::

    python tools/demo_online_reference.py                # offline + no key
    python tools/demo_online_reference.py --live         # also one real request
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai.deepseek_client import DeepSeekClient, describe_api_error  # noqa: E402
from app.ai.schemas import FoodItem  # noqa: E402
from app.ui.dialogs import ConfirmFoodDialog  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.ui.workers import ApiWorker  # noqa: E402
from app.utils.config import Settings, log_dir  # noqa: E402
from app.utils.logging_setup import setup_logging  # noqa: E402

SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"

FOODS = [
    FoodItem(
        name="鸡蛋",
        amount=2,
        unit="个",
        estimated_weight_g=100,
        calories_kcal=143,
        protein_g=12.6,
        carbs_g=0.7,
        fat_g=9.5,
        sugar_g=0.4,
        fibre_g=0,
        sodium_mg=142,
        confidence=0.9,
    ),
    FoodItem(
        name="全麦吐司",
        amount=1,
        unit="片",
        estimated_weight_g=35,
        calories_kcal=86,
        protein_g=4.6,
        carbs_g=14.4,
        fat_g=1.2,
        sugar_g=2.1,
        fibre_g=2.5,
        sodium_mg=140,
        confidence=0.9,
    ),
]


class _OfflineCompletions:
    """Raises the same error the SDK raises when it cannot reach the API."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise ConnectionError(
            "Connection error: offline demo - no socket was opened for this request"
        )


class _OfflineSdk:
    """Stands in for ``openai.OpenAI`` with the network unplugged."""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _OfflineCompletions()})()


def _pump(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def _dialog(client, *, title: str) -> ConfirmFoodDialog:
    dialog = ConfirmFoodDialog(
        FOODS,
        "manual",
        "",
        reference_job_factory=_factory(client),
        parent=None,
    )
    dialog.setWindowTitle(title)
    dialog.show()
    QApplication.processEvents()
    return dialog


def _factory(client):
    """Mirrors ``MainWindow.reference_job_factory``: connect, then start."""

    def factory(on_success, on_error, food_name: str) -> ApiWorker:
        worker = ApiWorker(
            lambda: client.fetch_nutrition_reference(food_name),
            label=f"online reference for {food_name!r}",
        )
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.start()
        return worker

    return factory


def _shot(dialog: ConfirmFoodDialog, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = SCREENSHOT_DIR / name
    dialog.grab().save(str(path))
    return path


def _card_state(card) -> str:
    """The button is always in the window; this says whether results are shown."""
    return "有内容" if card.is_showing() else "（只有按钮，没有内容）"


def scenario_offline(app: QApplication) -> int:
    """A key is set, but the API is unreachable. Nothing is shown."""
    settings = Settings(api_key="offline-demo", base_url="https://api.deepseek.com")
    sdk = _OfflineSdk()
    client = DeepSeekClient(settings, sdk_client=sdk)

    print("=== 场景 1：有 API Key，但网络不通 ===")
    print("  （HTTP 层被替换成直接抛连接错误，本场景不打开任何 socket）")
    dialog = _dialog(client, title="确认识别结果 - 断网演示")
    card = dialog.reference_card
    print(
        f"  点击之前：在线参考区 = {_card_state(card)}，"
        f"按钮可用 = {card.button().isEnabled()}，按钮可见 = {card.button().isVisibleTo(card)}"
    )
    before = _shot(dialog, "10a-online-reference-before-click.png")
    print(f"  点击前的截图（按钮在这里，还没有内容）：{before.relative_to(PROJECT_ROOT)}")

    card.button().click()
    # The request runs in a worker thread: give it a real chance to fail before
    # reading the result, instead of checking a state that has not changed yet.
    _pump(lambda: card.query_count == 0, timeout=0.2)
    time.sleep(0.5)
    _pump(lambda: card.has_result(), timeout=2.0)

    print(f"  点击之后：在线参考区 = {_card_state(card)}，按钮可用 = {card.button().isEnabled()}")
    print(f"  界面里的报错内容：{'（没有内容，也没有报错）' if not card.is_showing() else '（不该出现）'}")
    print(f"  本地参考/合计是否受影响：{'否' if dialog.table.rowCount() == len(FOODS) else '是'}")
    print(f"  合计仍然是：{dialog.totals_label.text()[:44]}…")
    print(f"  假 HTTP 层收到的请求次数：{sdk.chat.completions.calls}（真实网络请求：0）")
    print("  用户可见的弹窗：无（只有日志）")

    # What the log line looks like for a real translated error.
    try:
        client.fetch_nutrition_reference("鸡蛋")
    except Exception as exc:  # noqa: BLE001 - the demo prints it
        print(f"  同类错误的中文文案：{describe_api_error(exc)}")
    path = _shot(dialog, "10b-online-reference-offline-hidden.png")
    print(f"  点击后的截图（仍然没有在线参考区）：{path.relative_to(PROJECT_ROOT)}")
    dialog.close()
    return 0


def scenario_without_key(app: QApplication) -> int:
    """No API key: the button is disabled and explains itself."""
    print()
    print("=== 场景 2：没有 API Key ===")
    dialog = ConfirmFoodDialog(FOODS, "manual", "", reference_job_factory=None, parent=None)
    dialog.setWindowTitle("确认识别结果 - 未设置 API Key 演示")
    dialog.show()
    QApplication.processEvents()
    card = dialog.reference_card

    print(f"  按钮可用 = {card.button().isEnabled()}")
    print(f"  按钮提示 = {card.button().toolTip()}")
    card.button().click()  # 点了也不会有任何请求
    QApplication.processEvents()
    print(f"  点击之后：在线参考区 = {_card_state(card)}，已发起的查询次数 = {card.query_count}")
    path = _shot(dialog, "11-online-reference-without-key.png")
    print(f"  截图（按钮是灰的）：{path.relative_to(PROJECT_ROOT)}")
    dialog.close()
    return 0


def scenario_card_layout(app: QApplication) -> int:
    """Show what the card looks like when it has something to display.

    The online values come from the offline fake the tests use, so this scenario
    draws the layout without sending a request: same button, same background
    thread, same rendering code as a real answer.
    """
    from tests.fakes import FakeDeepSeekClient

    print()
    print("=== 场景 3：在线参考区有内容时的样子（离线假响应，不发请求）===")
    client = FakeDeepSeekClient()
    # 故意和本地参考值差得比较远（本地蛋 143 kcal），好让那句 30% 提示也露个面
    client.reference_payload = (
        '{"name":"鸡蛋","basis":"per_100g","calories_kcal":320,"protein_g":20,'
        '"carbs_g":3,"fat_g":27,"sugar_g":null,"fibre_g":0,"sodium_mg":124,'
        '"confidence":0.7,"note":""}'
    )
    dialog = _dialog(client, title="确认识别结果 - 在线参考区（示意）")
    card = dialog.reference_card
    card.button().click()
    if not _pump(lambda: card.has_result(), timeout=10):
        print("  没有渲染出在线参考区（这是工具本身的问题，不是程序行为）。")
        dialog.close()
        return 1
    dialog.resize(1120, 940)
    QApplication.processEvents()
    print(f"  在线参考区 = {_card_state(card)}")
    for field in (
        "calories_kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sugar_g",
        "fibre_g",
        "sodium_mg",
    ):
        print(f"  {field:14s} 在线显示：{card.online_text(field)}")
    print(f"  差异提示：{card.difference_label.text() or '（差异不到 30%，不提示）'}")
    path = _shot(dialog, "12-online-reference-result.png")
    print(f"  截图：{path.relative_to(PROJECT_ROOT)}")
    dialog.close()
    return 0


def scenario_live(app: QApplication) -> int:
    """One real request, to confirm the live path end to end."""
    print()
    print("=== 场景 4：真实请求（--live）===")
    settings = Settings.from_env()
    if not settings.has_api_key:
        print("  没有检测到 API Key，跳过。")
        return 2
    print(f"  模型：{settings.model}    接口地址：{settings.base_url}")
    dialog = _dialog(DeepSeekClient(settings), title="确认识别结果 - 在线参考")
    card = dialog.reference_card
    card.button().click()
    if not _pump(lambda: card.has_result(), timeout=60):
        print("  没有拿到可用的在线参考（离线或返回内容不合格式）——在线区保持隐藏。")
        dialog.close()
        return 1
    for field in (
        "calories_kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sugar_g",
        "fibre_g",
        "sodium_mg",
    ):
        print(f"  {field:14s} 在线显示：{card.online_text(field)}")
    print(f"  差异提示：{card.difference_label.text() or '（差异不到 30%，不提示）'}")
    path = _shot(dialog, "12-online-reference-result.png")
    print(f"  截图：{path.relative_to(PROJECT_ROOT)}")
    dialog.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="也执行一次真实的在线查询")
    args = parser.parse_args(argv)

    setup_logging(log_dir(), debug=False)
    app = QApplication.instance() or QApplication([])
    apply_app_style(app)

    print(f"日志文件：{log_dir() / 'app.log'}")
    print("（下面每个场景都会往日志里写一条 warning，界面本身保持安静）")
    print()
    code = scenario_offline(app)
    code = max(code, scenario_without_key(app))
    code = max(code, scenario_card_layout(app))
    if args.live:
        code = max(code, scenario_live(app))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
