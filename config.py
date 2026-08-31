"""Central configuration for the Vision-Language Desktop Agent."""
import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BACKEND = os.environ.get("VLA_BACKEND", "claude")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("VLA_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = 1024

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_raw_gemini_keys = os.environ.get("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _raw_gemini_keys.split(",") if k.strip()]
if not GEMINI_API_KEYS and GEMINI_API_KEY:
    GEMINI_API_KEYS = [GEMINI_API_KEY]

GEMINI_MODEL = os.environ.get("VLA_GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_MAX_ITERATIONS = 25
DEFAULT_MAX_MINUTES = 10
HISTORY_WINDOW = 8

ACTION_SETTLE_SECONDS = 1.0

SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_JPEG_QUALITY = 70

COMMON_BROWSER_WINDOW_HINTS = ["chrome", "edge", "firefox"]

HIGH_RISK_KEYWORDS = [
    "send",
    "delete",
    "remove",
    "purchase",
    "buy",
    "pay",
    "payment",
    "checkout",
    "submit payment",
    "confirm order",
    "transfer",
    "subscribe",
    "unsubscribe",
    "sign out",
    "log out",
    "deactivate",
    "cancel subscription",
    "post",
    "publish",
    "share publicly",
    "format",
    "reset",
    "uninstall",
    "shutdown",
    "restart computer",
]

RETROSPECTIVE_MARKERS = [
    "previous attempt",
    "previous attempts",
    "last attempt",
    "my last",
    "did not",
    "didn't",
    "failed",
    "unsuccessful",
    "resulted in",
    "consistently",
    "repeatedly",
    "was not",
    "wasn't",
    "has not",
    "hasn't",
]

HIGH_RISK_KEY_COMBOS = [
    "alt+f4",
    "ctrl+w",
    "ctrl+q",
    "ctrl+alt+delete",
    "win+l",
]

KILL_SWITCH_PHRASE = "stop"

CHECKIN_EVERY_N_ITERATIONS = 15

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
