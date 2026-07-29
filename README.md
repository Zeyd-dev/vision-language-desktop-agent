# Vision-Language Desktop Agent

An autonomous agent that takes a natural-language instruction, observes your
screen, and completes the task by controlling the mouse and keyboard — one
action at a time, in a loop, until it's done or it gives up safely.

```
screenshot -> VLM (perceive + decide) -> safety check -> execute one action -> repeat
```

Three interfaces share the exact same loop: a CLI, a desktop GUI, and a
local web UI you can also reach from your phone.

## Project layout

```
agent.py             CLI entry point (stdin kill switch + confirmations)
gui.py                Tkinter desktop GUI (task box, run/stop, live log)
webapp/
  app.py              Flask web UI -- same loop, reachable from a browser or phone
  templates/index.html
core/
  loop.py             The actual screenshot -> decide -> safety-check -> execute
                      loop, shared by all three interfaces above
config.py             All tunables (models, caps, safety keywords, paths)
run_logger.py         Writes runs/<timestamp>/ logs for after-the-fact review
backends/
  base.py             VLMBackend interface + AgentAction schema (model-agnostic)
  claude_backend.py    Claude implementation, uses tool-use to force valid JSON
  gemini_backend.py    Gemini implementation (free tier), uses response_schema
                      to force valid JSON, plus multi-key rotation/rate pacing
  prompts.py           The system prompt given to the model
actions/
  executor.py          mss screenshot capture + pyautogui execution, plus
                      open_url/launch_app/focus_window (direct OS calls,
                      skip the GUI)
  safety.py             High-risk keyword detection, confirmation prompts, kill switch
runs/                  Created automatically; one folder per run
```

## Setup (Windows)

1. **Install Python 3.11+** if you don't have it, from python.org. During
   install, check "Add python.exe to PATH".

2. **Create a virtual environment and install dependencies** (PowerShell or
   cmd, from this folder):

   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Set your API key(s).** Copy `.env.example` to `.env`:

   ```
   copy .env.example .env
   ```

   Then edit `.env`. You need at least one of:
   - `GEMINI_API_KEY` — free tier, get one at https://aistudio.google.com/apikey.
     Optionally set `GEMINI_API_KEYS=key1,key2,...` for automatic fallback
     when one key's daily quota is exhausted.
   - `ANTHROPIC_API_KEY` — paid, get one at https://console.anthropic.com/.
     A Claude.ai Pro/Max subscription does **not** include API access — the
     API is billed separately.

   `VLA_BACKEND` picks the default (`claude` or `gemini`) when a backend
   isn't explicitly chosen.

4. **Display scaling note:** if your display is set to anything other than
   100% scaling (Settings > System > Display > Scale), clicks can land in
   the wrong place because pyautogui and screenshots may disagree about
   pixel coordinates. `actions/executor.py` calls Windows' DPI-awareness API
   on startup to fix this automatically for most setups; `agent.py`/`gui.py`/
   `webapp/app.py` also print a loud warning at startup if a mismatch is
   still detected.

5. **Antivirus/UAC:** pyautogui simulates real mouse/keyboard input. Some
   antivirus software flags this behavior — if the agent's clicks don't
   register, check whether your AV is silently blocking synthetic input.

## Usage

**CLI:**
```
python agent.py "Open a browser and go to YouTube"
python agent.py "your task" --backend gemini --max-iterations 15 --max-minutes 5
```

**Desktop GUI:**
```
python gui.py
```

**Web UI (also reachable from your phone):**
```
python webapp/app.py
```
This prints two URLs (localhost, and one for your phone) plus a scannable
QR code in the terminal. The same QR code also appears **inside the page
itself** — a "Connect your phone" card shows it as a live image (not just
ASCII text) alongside the tappable URL, so you don't need to go find the
terminal window to scan it. That card only shows up when you're viewing the
page on the PC; if you open the page on the phone itself, it detects that
and shows a "you're already connected" message instead, since there's
nothing to scan from there. Your phone must be on the **same wifi network**
as this PC. If the phone can't connect, check Windows Firewall — you may
need to allow Python/port 5000 through it for private networks (Windows
Security > Firewall & network protection > Allow an app).

**One-time setup, then no more scanning:** after the first visit, use your
phone browser's menu to "Add to Home Screen." That installs a proper icon
(uses the manifest/icons in `webapp/static/`) so future runs are one tap,
not a re-scan.

The page itself is built to be understandable at a glance, not just for
whoever wrote it:
- A **result banner** at the very top of the page shows the task's actual
  outcome in large, color-coded text the moment it finishes (green for
  done, red for failed/stopped, amber for hit-a-limit) -- e.g. "There are
  8 spam messages" -- instead of making you dig through the log to find
  the answer. It has copy and dismiss buttons, and survives a page reload
  or reconnecting from your phone later, since it's driven by server-side
  state rather than the live log stream.
- A **plain-language status line** ("Thinking: ...", "Clicking", "Opening a
  website"...) sits above the raw log, which is now tucked into a collapsed
  "Technical log" section for anyone who wants the detail.
- A **live screenshot preview** shows what the agent is currently looking
  at, refreshed every couple of seconds during a run.
- **Example task chips** for one-tap common tasks, and a **recent runs**
  list (task / status) so history doesn't require digging through `runs/`.

Note: if you open the web UI in a browser *on the same PC* the agent is
controlling, that browser window will be visible in screenshots, the same
way the desktop GUI's own window used to be before it started minimizing
itself. Prefer controlling it from your phone, or minimize the browser
manually after starting a run.

While a run is active, all three interfaces offer:
- **Kill switch:** CLI — type `stop` + Enter. GUI/web — click Stop. Checked
  before every action and while any confirmation prompt is on screen.
- **Physical failsafe:** slam the mouse into any screen corner. pyautogui
  aborts immediately (`pyautogui.FAILSAFE = True`).
- **High-risk confirmations:** if the model's own reasoning (or the text
  it's about to type) contains a word like "send", "delete", "purchase",
  "submit payment", "sign out", etc. (see `HIGH_RISK_KEYWORDS` in
  `config.py`), the agent pauses and asks you to approve before executing
  that specific action. This is a word-boundary text match on the model's
  own phrasing, not a semantic understanding of risk — a differently-worded
  description of the same risky action can slip through uncaught.
- **Periodic check-ins (CLI only):** every 15 iterations (configurable via
  `CHECKIN_EVERY_N_ITERATIONS`), the CLI stops and asks whether to keep
  going, regardless of the iteration/time caps.

## Reviewing a run

Every run writes to `runs/<timestamp>/`:

- `log.txt` — human-readable, step-by-step reasoning and actions
- `log.jsonl` — the same data as one JSON object per line
- `screenshots/step_NNN.jpg` — full-resolution screenshot for each step
- `summary.json` — final status (`done`, `fail`, `iteration_limit`,
  `time_limit`, or `killed`) and detail message

## How coordinates work

Screenshots are downscaled (max width 1280px by default, see
`SCREENSHOT_MAX_WIDTH` in `config.py`) before being sent to the model, to
keep image tokens and latency down. The model's `coordinates` are always
relative to the *downscaled* image it was shown. `Screenshot.to_real_coords()`
rescales them back to real screen pixels before `pyautogui` executes the
click, and `Screenshot.is_within_bounds()` rejects any coordinate the model
returns that falls outside the image it was actually shown, rather than
scaling and clicking blind. Both backends also state the exact downscaled
width/height as plain text in the prompt each turn, so the model has an
explicit number to stay within instead of estimating from the image alone.

## Extending

- **Swap models:** implement `VLMBackend.decide()` (see `backends/base.py`)
  for another provider; `backends/__init__.py`'s `get_backend()` is the only
  place that needs to know it exists.
- **Add a new interface:** call `core.loop.run_agent_loop()` with a
  `LoopCallbacks` (log/confirm/should_stop) adapted to your frontend — see
  `agent.py`, `gui.py`, or `webapp/app.py` for three working examples.
- **Add a reflect/replan step:** `AgentAction` already carries an optional
  `expected_outcome` field the model fills in each turn. A future version
  can capture the *next* screenshot, compare it against that expectation,
  and explicitly branch on "this didn't do what I expected" instead of
  always trusting the next `decide()` call to notice on its own.

## Known limitations

- Single monitor only (primary display).
- No OCR/accessibility-tree grounding — click accuracy depends entirely on
  the model's pixel estimate from the screenshot.
- The high-risk keyword check is a word-boundary text match on the model's
  own reasoning, not a semantic understanding of risk — treat it as a speed
  bump, not a guarantee. A real test run asking the agent to send an email
  never triggered the check because the model's reasoning talked about
  "compose"/"type" and never used the literal word "send". Dangerous key
  combos (alt+f4, ctrl+w, ctrl+q, ctrl+alt+delete, win+l — see
  `HIGH_RISK_KEY_COMBOS` in `config.py`) are now checked directly against
  the `key` field regardless of reasoning wording, closing that specific
  gap, but the reasoning-text check itself is still just text matching.
- No reliable way to bring an already-open window to the foreground existed
  until `focus_window` was added — a real run asked to open Notepad burned
  20 steps toggling between windows via guessed taskbar clicks, blind
  alt-tab, and repeated re-launching before giving up. `focus_window`
  matches a window title substring directly (Windows `EnumWindows` +
  `SetForegroundWindow`) instead of guessing pixels.
- A related bug (now fixed) meant the model was never told the screenshot's
  actual pixel dimensions — one backend had a dead placeholder line that
  never rendered real numbers, the other never mentioned size at all, and
  the loop was passing the *real* monitor resolution instead of the
  *downscaled* image size actually shown. Both backends now state the
  exact width/height of the image the model is looking at, in its own
  coordinate space.
- "Wrong clicks" on dynamic pages (a real run repeatedly missed Gmail's
  Compose button) traced back to a timing gap: coordinates are computed
  from a screenshot taken *before* the model's decide() round-trip, which
  can take several seconds -- long enough for a page to reflow before the
  click actually fires, especially on Gemini's paced free tier (~13s
  minimum between calls). `execute_action()` now re-screenshots
  immediately before firing a click/double_click and skips it if the
  screen has changed since the decision was made, rather than confidently
  clicking a target that's no longer there.
- A real "send an email" run had the recipient address and the subject
  line end up concatenated in the same "To" field. Root cause: the "type"
  action never actually used any coordinates the model supplied -- it just
  typed into whatever already had keyboard focus. The model clicked/typed
  the recipient, then typed the subject at different coordinates without
  ever tabbing or clicking away first, so both landed in the same still-
  focused field. Fixed: a "type" action with coordinates now clicks that
  spot first (with the same out-of-bounds/staleness guards as a real
  click) before typing, so field focus is explicit instead of assumed.
- That same run also showed `screen_changed=False` for 13+ consecutive
  steps across many different click targets, which fed false "nothing is
  working" signals into both the model's history and the stuck-loop
  nudge. Root cause: `screens_differ()` diffs the *whole* screen -- a
  small popup closing or one field updating barely moves the mean pixel
  difference on a large monitor, so a real, working action can still be
  reported as a no-op. Fixed: the loop now also runs a localized diff
  (`crop_region()`) around the exact point of the last click/type, and
  treats the action as having worked if *either* check detects a change.
- A further run showed the model proposing coordinates that exactly matched
  the *real screen pixel* value from the previous step, instead of a fresh
  image-space guess — e.g. it clicked at real pixel (84, 261) on step 2, then
  on step 3 proposed coordinates [84, 261] again, even though its own
  image-space input on step 2 was [56, 174]. Root cause: `execute_action()`
  was reporting the real, post-scaling screen pixels in the outcome string
  written back into the model's own history, even though the model is
  instructed throughout the prompt to always reason in downscaled image-
  space coordinates. The model was reading its own history, seeing a number
  in the wrong coordinate space, and mistaking it for a valid future
  image-space value. Fixed: outcome strings for click/double_click/type-
  with-coordinates now report the model's original image-space coordinates;
  the real screen pixels are still used (and returned separately) for actual
  execution and for the localized region-diff check, so nothing about
  execution accuracy changed — only what the model is told about what it did.
- `focus_window` only sees separate OS-level windows (Notepad, a whole
  browser, VS Code) — it cannot see or focus elements *inside* a page, like
  Gmail's compose box or a modal. A run tried `focus_window("New Message")`
  on an in-page Gmail panel and got a correct "not found" result, but wasted
  a step getting there. The system prompt now explicitly states this
  limitation and tells the model to click/scroll to in-page panels instead.
- The web UI's friendly status headline never showed the task's actual
  result. `core/loop.py` sends its completion line as `"\nDONE: {detail}"`
  (leading newline, for readable terminal/GUI output), but
  `webapp/app.py`'s status-line matcher checked `msg.startswith("DONE:")` --
  which is `False` for a string starting with `\n`. The headline silently
  got stuck on "Finishing up" forever, even though the agent had genuinely
  finished and the real answer (e.g. "There are 8 spam messages") was
  sitting in the raw "Technical log" section the whole time, just not
  surfaced where anyone would look. Same mismatch affected the "FAILED:"
  and "Run finished" lines. Fixed by stripping the message before matching;
  verified with a scripted test simulating the exact log sequence a real
  "done" run produces.
- The web UI has no authentication. Binding to `0.0.0.0` so your phone can
  reach it means *any* device on the same wifi network can reach it too --
  there's currently no login or PIN gating who can run tasks, watch the
  live screenshot, or stop a run. Fine on a trusted home network; a real
  gap on shared/public wifi. Planned next, not yet built.
- One run at a time across all three interfaces (CLI, GUI, web) — there's a
  single global loop, not per-tab/per-device concurrency.
- No formal accuracy benchmark yet — testing so far has been informal, via
  real runs and manual log review rather than a repeatable task suite.
