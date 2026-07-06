"""
Screen capture and OS-level input execution.

Wraps `mss` (screenshots) and `pyautogui` (mouse/keyboard) behind a small
ActionExecutor class, plus a Screenshot capture helper that produces both a
full-resolution image (for logging) and a downscaled JPEG (for the VLM),
tracking the scale factor so model-provided coordinates can be mapped back
to real screen pixels.
"""
from __future__ import annotations

import io
import platform
import re
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import quote_plus

import mss
import pyautogui
from PIL import Image

from config import SCREENSHOT_JPEG_QUALITY, SCREENSHOT_MAX_WIDTH

# pyautogui safety net: slamming the mouse into a screen corner aborts
# whatever pyautogui is doing (raises FailSafeException).
pyautogui.FAILSAFE = True
# Small delay between pyautogui calls so the OS/UI has time to react.
pyautogui.PAUSE = 0.15


def _enable_windows_dpi_awareness() -> None:
    """
    On Windows, display scaling above 100% makes pyautogui's coordinate
    space diverge from raw screenshot pixels, so clicks land in the wrong
    spot. Marking this process DPI-aware fixes that for most setups. Safe
    no-op on other platforms or if the underlying Windows API is missing.
    """
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # best effort; see README for manual scaling workaround


_enable_windows_dpi_awareness()


@dataclass
class Screenshot:
    full_image: Image.Image  # native resolution, kept for saving to the run log
    api_bytes: bytes  # downscaled JPEG bytes, sent to the VLM
    real_size: Tuple[int, int]  # (width, height) of full_image
    resized_size: Tuple[int, int]  # (width, height) encoded in api_bytes
    scale: float  # real_size / resized_size (uniform across both axes)

    def to_real_coords(self, x: float, y: float) -> Tuple[int, int]:
        """Map a coordinate given in the downscaled (API) image back to real screen pixels."""
        return int(round(x * self.scale)), int(round(y * self.scale))

    def is_within_bounds(self, x: float, y: float) -> bool:
        """
        True if (x, y) falls inside the downscaled image the model was
        actually shown. Models occasionally invent coordinates outside the
        image entirely (e.g. a y-value beyond the image's real height) --
        scaling and clicking one of those anyway lands somewhere on the
        real screen that has nothing to do with what the model intended.
        Rejecting it up front is safer than clicking blind.
        """
        w, h = self.resized_size
        return 0 <= x <= w and 0 <= y <= h


def get_monitor_size() -> Tuple[int, int]:
    """Physical pixel size mss reports for the primary monitor."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        return monitor["width"], monitor["height"]


def get_pyautogui_size() -> Tuple[int, int]:
    """Screen size pyautogui believes it's operating in for mouse moves/clicks."""
    return pyautogui.size()


def screens_differ(img_a: Image.Image, img_b: Image.Image, threshold: float = 0.015) -> bool:
    """
    Cheap perceptual diff between two full-resolution screenshots.

    Downscales both to a small grayscale thumbnail and compares mean pixel
    difference. This gives an OBJECTIVE, code-computed signal for "did
    anything on screen actually change after the last action" -- as
    opposed to relying on the model's own self-reported judgment call,
    which real runs have shown can be wrong or can get stuck re-asserting
    the same read of the screen turn after turn. The loop controller uses
    this to detect stuck loops and force a strategy change.
    """
    size = (96, 96)
    a = img_a.convert("L").resize(size)
    b = img_b.convert("L").resize(size)
    a_bytes = a.tobytes()
    b_bytes = b.tobytes()
    diff_sum = sum(abs(x - y) for x, y in zip(a_bytes, b_bytes))
    mean_diff = diff_sum / (len(a_bytes) * 255)
    return mean_diff > threshold


def capture_screenshot() -> Screenshot:
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary monitor; monitors[0] is "all monitors" combined
        raw = sct.grab(monitor)
        full_image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    real_w, real_h = full_image.size
    scale = 1.0
    resized = full_image
    if real_w > SCREENSHOT_MAX_WIDTH:
        scale = real_w / SCREENSHOT_MAX_WIDTH
        resized_h = int(round(real_h / scale))
        resized = full_image.resize((SCREENSHOT_MAX_WIDTH, resized_h), Image.LANCZOS)

    buf = io.BytesIO()
    resized.convert("RGB").save(buf, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY)

    return Screenshot(
        full_image=full_image,
        api_bytes=buf.getvalue(),
        real_size=(real_w, real_h),
        resized_size=resized.size,
        scale=scale,
    )


class ActionExecutor:
    """Executes a single AgentAction against the real OS via pyautogui."""

    def click(self, x: int, y: int) -> None:
        pyautogui.click(x, y)

    def double_click(self, x: int, y: int) -> None:
        pyautogui.doubleClick(x, y)

    def type_text(self, text: str) -> None:
        pyautogui.write(text, interval=0.02)

    def press_key(self, key: str) -> None:
        # Support combos like "ctrl+l" as well as single keys like "enter".
        parts = [p.strip() for p in key.lower().split("+") if p.strip()]
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        elif parts:
            pyautogui.press(parts[0])

    def scroll(self, amount: Optional[int]) -> None:
        # Schema convention: positive = scroll down, negative = scroll up.
        # pyautogui.scroll uses the opposite sign and small integer "clicks".
        clicks = amount if amount else 3
        pyautogui.scroll(-clicks * 40)

    def wait(self, seconds: float = 1.5) -> None:
        time.sleep(seconds)

    def open_url(self, url_or_query: str) -> str:
        """
        Deterministically opens a URL (or a search query) in the OS default
        browser via the stdlib `webbrowser` module, WITHOUT touching the
        Start menu, taskbar icons, or any key-press sequence at all.

        This exists because the GUI-only path -- press Windows key, hope
        the Start menu actually opened (it can silently toggle closed on a
        second press), type an app name into a search box that may or may
        not be focused, press Enter and hope -- is inherently a multi-step
        race across several slow model round-trips. Real runs have shown
        this failing for 15-20 iterations straight even when nothing looks
        obviously wrong. Opening a browser to a URL is a single, atomic,
        OS-level call with no such race, so it should be preferred whenever
        the task is "get to this website" rather than reproduced by hand.

        If `url_or_query` isn't already a URL, it's treated as a search
        term and wrapped into a Google search URL.
        """
        target = url_or_query.strip()
        if not re.match(r"^https?://", target, re.IGNORECASE):
            if "." in target and " " not in target:
                target = f"https://{target}"
            else:
                target = f"https://www.google.com/search?q={quote_plus(target)}"
        webbrowser.open(target)
        return target

    def launch_app(self, name: str) -> str:
        """
        Deterministically launches a named application (e.g. "chrome",
        "notepad", "calc", "explorer") the same way typing it into the
        Windows Run dialog and pressing Enter would -- but as one direct
        OS call instead of several GUI steps (open Start menu, confirm it's
        focused, type, press Enter) that can silently fail or race against
        the next screenshot.
        """
        name = name.strip()
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        return name
