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
        """True if (x, y) falls inside the downscaled image the model was actually shown."""
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
    """Cheap perceptual diff: downscale both to a grayscale thumbnail and compare mean pixel difference."""
    size = (96, 96)
    a = img_a.convert("L").resize(size)
    b = img_b.convert("L").resize(size)
    a_bytes = a.tobytes()
    b_bytes = b.tobytes()
    diff_sum = sum(abs(x - y) for x, y in zip(a_bytes, b_bytes))
    mean_diff = diff_sum / (len(a_bytes) * 255)
    return mean_diff > threshold


def crop_region(img: Image.Image, x: int, y: int, box: int = 180) -> Image.Image:
    """Crop a `box`-pixel square centered on real screen coordinates (x, y), clamped to the image.

    Used to supplement screens_differ() with a localized check: a small UI
    change (a compose popup closing, one field updating) can be too small
    to move a whole-screen diff, even though the action genuinely worked --
    diffing just the area around where the action happened catches that."""
    w, h = img.size
    half = box // 2
    left = max(0, min(x - half, w - box)) if w > box else 0
    top = max(0, min(y - half, h - box)) if h > box else 0
    right = min(w, left + box)
    bottom = min(h, top + box)
    return img.crop((left, top, right, bottom))


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
        """Open a URL (or search query) in the OS default browser directly, no GUI navigation involved."""
        target = url_or_query.strip()
        if not re.match(r"^https?://", target, re.IGNORECASE):
            if "." in target and " " not in target:
                target = f"https://{target}"
            else:
                target = f"https://www.google.com/search?q={quote_plus(target)}"
        webbrowser.open(target)
        return target

    def launch_app(self, name: str) -> str:
        """Launch a named app directly (same mechanism as the Windows Run dialog), no GUI steps involved."""
        name = name.strip()
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        return name

    def focus_window(self, name: str) -> str:
        """Bring an already-open window to the foreground by matching a substring of its title.

        This exists because clicking a taskbar icon or alt-tabbing blind is one of the
        least reliable things the model can do -- it has to guess pixel coordinates for
        an icon it can't precisely locate, or cycle windows with no way to confirm which
        one landed in front. This gives a deterministic alternative: find the window,
        raise it, done in one step -- no guessing involved.
        """
        if platform.system() != "Windows":
            return f"focus_window not supported on this OS (skipped): {name}"

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        needle = name.strip().lower()
        matches: list[tuple[int, str]] = []

        def _enum_callback(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if needle in buf.value.lower():
                        matches.append((hwnd, buf.value))
            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_enum_callback)
        user32.EnumWindows(enum_proc, 0)

        if not matches:
            return f"no open window found matching '{name}' -- it may not be open yet"

        hwnd, title = matches[0]
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)  # un-minimize if needed

        # SetForegroundWindow is blocked by Windows for background processes unless
        # our thread's input state is briefly attached to the current foreground
        # window's thread -- the standard workaround for this restriction.
        fg_hwnd = user32.GetForegroundWindow()
        current_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        user32.AttachThreadInput(current_thread, fg_thread, True)
        user32.AttachThreadInput(current_thread, target_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current_thread, fg_thread, False)
        user32.AttachThreadInput(current_thread, target_thread, False)

        return f"focused window: {title!r}"
