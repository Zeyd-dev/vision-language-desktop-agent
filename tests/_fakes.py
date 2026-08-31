"""
Shared test scaffolding: install lightweight fake `pyautogui` and `mss`
modules into sys.modules BEFORE any project module is imported.

Why: actions/executor.py imports pyautogui and mss at module scope, and
pyautogui raises immediately on import in any environment without a real
display (headless CI, this test runner, a server). The pure-logic pieces
under test here never need a real screen or real mouse/keyboard -- they
just need executor.py (and anything that imports it) to import
successfully. Call install_fakes() at the top of any test module that
directly or indirectly imports actions.*, backends.*, or core.loop, before
those imports happen.
"""
from __future__ import annotations

import sys
import types


def install_fakes() -> None:
    if "pyautogui" not in sys.modules:
        fake_pyautogui = types.ModuleType("pyautogui")
        fake_pyautogui.FAILSAFE = False
        fake_pyautogui.PAUSE = 0
        fake_pyautogui.click = lambda *a, **k: None
        fake_pyautogui.doubleClick = lambda *a, **k: None
        fake_pyautogui.write = lambda *a, **k: None
        fake_pyautogui.press = lambda *a, **k: None
        fake_pyautogui.hotkey = lambda *a, **k: None
        fake_pyautogui.scroll = lambda *a, **k: None
        fake_pyautogui.size = lambda: (1920, 1080)

        class _FailSafeException(Exception):
            pass

        fake_pyautogui.FailSafeException = _FailSafeException
        sys.modules["pyautogui"] = fake_pyautogui

    if "mss" not in sys.modules:
        fake_mss_module = types.ModuleType("mss")

        class _FakeMSS:
            monitors = [
                {"width": 1920, "height": 1080},  # index 0: "all monitors"
                {"width": 1920, "height": 1080},  # index 1: primary
            ]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def grab(self, monitor):
                class _Raw:
                    size = (2, 2)
                    bgra = b"\x00\x00\x00\xff" * 4

                return _Raw()

        fake_mss_module.mss = _FakeMSS
        sys.modules["mss"] = fake_mss_module
