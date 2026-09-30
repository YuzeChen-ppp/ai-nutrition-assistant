"""临时调试工具：运行一个测试，卡住 15 秒后打印所有线程的调用栈。

用法：python tools/hang_probe.py tests.test_ui_api_key_flow.ApiKeyFallbackTest
"""

from __future__ import annotations

import faulthandler
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

target = sys.argv[1] if len(sys.argv) > 1 else "tests"
faulthandler.enable()
faulthandler.dump_traceback_later(15, exit=True)

loader = unittest.TestLoader()
if Path(target).is_dir() or "." not in target:
    suite = loader.discover(start_dir=target, top_level_dir=str(ROOT))
else:
    suite = loader.loadTestsFromName(target)
result = unittest.TextTestRunner(verbosity=2).run(suite)
faulthandler.cancel_dump_traceback_later()
raise SystemExit(0 if result.wasSuccessful() else 1)
