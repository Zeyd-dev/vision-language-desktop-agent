"""
Central configuration for the Vision-Language Desktop Agent.

All tunables live here so behavior can be adjusted without digging through
the loop controller or backend code.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- VLM backend ---
# Which backend agent.py uses by default when --backend isn't passed on the
# command line. "claude" needs a paid ANTHROPIC_API_KEY; "gemini" works on
# Google's free API tier.
DEFAULT_BACKEND = os.environ.get("VLA_BACKEND", "claude")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Vision-capable Claude model used for perceive+decide each loop iteration.
CLAUDE_MODEL = os.environ.get("VLA_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = 1024

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Optional pool of keys for automatic fallback: GEMINI_API_KEYS="key1,key2,key3"
# in .env. Each free-tier Google Cloud project gets its own independent daily
# quota, so when one key's daily cap is hit, GeminiBackend rotates to the next
# one automatically instead of just failing. Falls back to GEMINI_API_KEY
# alone if GEMINI_API_KEYS isn't set.
_raw_gemini_keys = os.environ.get("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _raw_gemini_keys.split(",") if k.strip()]
if not GEMINI_API_KEYS and GEMINI_API_KEY:
    GEMINI_API_KEYS = [GEMINI_API_KEY]

# General-purpose vision-capable Gemini model (not the browser-only Computer
# Use preview model) -- this is what lets GeminiBackend reuse our own
# AgentAction schema instead of a browser-specific action format.
GEMINI_MODEL = os.environ.get("VLA_GEMINI_MODEL", "gemini-2.5-flash")

# --- Loop controller ---
DEFAULT_MAX_ITERATIONS = 25
DEFAULT_MAX_MINUTES = 10
# How many prior steps (reasoning + action summary, no images) to keep in
# the condensed history sent to the model each turn.
HISTORY_WINDOW = 8

# How long to pause after executing an action before the NEXT screenshot is
# captured. Pages (especially search-result pages with async-loading panels,
# images, ads) can still be shifting layout right after a click/navigation;
# screenshotting too early captures a mid-shift state, which both misleads
# the model and makes screens_differ() see "no change" for an action that
# actually did something, just not yet visibly. This is intentionally short
# -- it's a settle beat, not a full page-load wait (use the "wait" action
# for that).
ACTION_SETTLE_SECONDS = 1.0

# --- Screenshot handling ---
# Screenshots are downscaled before being sent to the API to keep tokens/
# latency down. Coordinates returned by the model are in the *downscaled*
# image's coordinate space and are rescaled back up before executing.
SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_JPEG_QUALITY = 70

# --- Safety guardrails ---
# Any action whose model-provided reasoning (or nearby visible text, when
# available) contains one of these substrings requires an explicit typed
# "y" confirmation in the terminal before it is executed.
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

# Kill switch: typing this word (+ Enter) in the terminal from the monitor
# thread halts the loop immediately, before the next action executes.
KILL_SWITCH_PHRASE = "stop"

# Safety check-in: even if under the iteration/time caps, ask the user to
# confirm continuation every N iterations for long-running tasks.
CHECKIN_EVERY_N_ITERATIONS = 15

# --- Logging ---
RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
