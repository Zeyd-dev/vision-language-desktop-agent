"""Screen capture and OS-level input execution."""
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

from config import COMMON_BROWSER_WINDOW_HINTS, SCREENSHOT_JPEG_QUALITY, SCREENSHOT_MAX_WIDTH

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15


def _enable_windows_dpi_awareness() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_windows_dpi_awareness()


@dataclass
class Screenshot:
    full_image: Image.Image
    api_bytes: bytes
    real_size: Tuple[int, int]
    resized_size: Tuple[int, int]
    scale: float

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
    """Crop a `box`-pixel square centered on real screen coordinates (x, y), clamped to the image."""
    w, h = img.size
    half = box // 2
    left = max(0, min(x - half, w - box)) if w > box else 0
    top = max(0, min(y - half, h - box)) if h > box else 0
    right = min(w, left + box)
    bottom = min(h, top + box)
    return img.crop((left, top, right, bottom))


def capture_screenshot() -> Screenshot:
    with mss.mss() as sct:
        monitor = sct.monitors[1]
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
        parts = [p.strip() for p in key.lower().split("+") if p.strip()]
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        elif parts:
            pyautogui.press(parts[0])

    def scroll(self, amount: Optional[int]) -> None:
        clicks = amount if amount else 3
        pyautogui.scroll(-clicks * 40)

    def wait(self, seconds: float = 1.5) -> None:
        time.sleep(seconds)

    def _bring_forward_after_open(self, name_hints: list, settle_seconds: float = 0.8) -> Optional[str]:
        """Best-effort: after opening a URL or launching an app, the OS."""
        if platform.system() != "Windows":
            return None
        time.sleep(settle_seconds)
        for name in name_hints:
            result = self.focus_window(name)
            if result.startswith("focused window:"):
                return f"brought '{name}' window to the foreground"
        return None

    def open_url(self, url_or_query: str) -> str:
        """Open a URL (or search query) in the OS default browser directly, no GUI navigation involved."""
        target = url_or_query.strip()
        if not re.match(r"^https?://", target, re.IGNORECASE):
            if "." in target and " " not in target:
                target = f"https://{target}"
            else:
                target = f"https://www.google.com/search?q={quote_plus(target)}"
        webbrowser.open(target)
        note = self._bring_forward_after_open(COMMON_BROWSER_WINDOW_HINTS)
        return f"{target} [{note}]" if note else target

    def launch_app(self, name: str) -> str:
        """Launch a named app directly (same mechanism as the Windows Run dialog), no GUI steps involved."""
        name = name.strip()
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        note = self._bring_forward_after_open([name])
        return f"{name} [{note}]" if note else name

    def focus_window(self, name: str) -> str:
        """Bring an already-open window to the foreground by matching a substring of its title."""
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
        user32.ShowWindow(hwnd, SW_RESTORE)

        def _attempt_foreground() -> bool:
            """Try to raise hwnd and report whether it actually became the foreground window."""
            fg_hwnd = user32.GetForegroundWindow()
            current_thread = kernel32.GetCurrentThreadId()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)
            user32.AttachThreadInput(current_thread, fg_thread, True)
            user32.AttachThreadInput(current_thread, target_thread, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(current_thread, fg_thread, False)
            user32.AttachThreadInput(current_thread, target_thread, False)
            return user32.GetForegroundWindow() == hwnd

        if not _attempt_foreground():
            VK_MENU = 0x12
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            _attempt_foreground()

        if user32.GetForegroundWindow() == hwnd:
            return f"focused window: {title!r}"
        return (
            f"found window {title!r} but Windows blocked bringing it to the "
            f"foreground (another app is likely holding focus) -- try clicking "
            f"directly on the visible window instead of retrying focus_window"
        )

    def find_element_bounds(self, name_hint: str) -> Optional[Tuple[int, int, int, int]]:
        """Look up a real on-screen control's exact bounding box by name, via."""
        if platform.system() != "Windows":
            return None
        try:
            import uiautomation as auto
        except ImportError:
            return None

        needle = (name_hint or "").strip().lower()
        if not needle:
            return None

        try:
            root = auto.GetForegroundControl()
            if root is None:
                return None
            MAX_NODES = 800
            MAX_DEPTH = 12
            stack: list[tuple[object, int]] = [(root, 0)]
            visited = 0
            while stack and visited < MAX_NODES:
                node, depth = stack.pop()
                visited += 1
                try:
                    name = (node.Name or "").strip().lower()
                except Exception:
                    name = ""
                if name and needle in name:
                    try:
                        rect = node.BoundingRectangle
                        if rect and rect.width() > 0 and rect.height() > 0:
                            return (rect.left, rect.top, rect.right, rect.bottom)
                    except Exception:
                        pass
                if depth < MAX_DEPTH:
                    try:
                        for child in node.GetChildren():
                            stack.append((child, depth + 1))
                    except Exception:
                        pass
            return None
        except Exception:
            return None
