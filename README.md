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
- **Reflect step: built.** `AgentAction` carries `expected_outcome` (the
  model's own prediction) and `expectation_met` (the model's next-turn
  verdict on whether that prediction came true, checked against the actual
  screenshot). `run_agent_loop()` backfills `expectation_met` onto the
  relevant history entry and folds it into stuck-detection alongside
  `screen_changed` -- see "Reliability improvements" below.

## Reliability improvements (this round)

Six changes made together, aimed at multi-step "hard" tasks (form-filling,
email) rather than any single bug, plus two more real bugs found and fixed
while testing them. All pass real automated tests in `tests/` (81 tests)
run in this environment -- but most have not been exercised against a live
Windows screen yet. Treat anything GUI-dependent below as "compiles and
passes its mocked tests," not "verified working," until confirmed on a real
run.

- **UI Automation cross-check for clicks.** `ActionExecutor.find_element_bounds()`
  looks up a real on-screen control's exact bounding box via Windows UI
  Automation (the same tree screen readers use) when the model supplies an
  optional `target_hint` (e.g. `"Compose button"`). If a match is found
  within 150px of the model's own pixel guess, the click snaps to the
  element's real center instead of the estimate. Falls back to the raw
  guess unchanged if UI Automation isn't available, the optional
  `uiautomation` package isn't installed, or nothing matches -- this can
  never make a click less reliable than before, only more precise when it
  has real data to work with. Untested against a real screen so far.
- **Reflect step (verify, don't just trust).** Every action includes
  `expected_outcome`; the following turn's `expectation_met` is the model's
  own honest comparison against what actually happened, which catches a
  case `screen_changed` can't -- the screen changing into the *wrong* thing
  (an error dialog instead of the target page) rather than not changing at
  all.
- **Structured `risk_level` on every action.** A third, independent signal
  alongside the existing keyword and key-combo checks: the model
  self-assesses `low`/`medium`/`high` risk on every single action, with
  explicit criteria in the system prompt, regardless of which words it used
  in `reasoning`. Closes the exact gap documented above (a run describing a
  send using only "compose"/"submit"): `is_self_reported_high_risk()` in
  `actions/safety.py` fires independently of the wording check.
- **Repeatable benchmark suite.** `benchmarks/tasks.json` (16 tasks, easy/
  medium/hard) and `benchmarks/run_benchmark.py` run the real agent loop
  against each and record pass/fail/duration to `benchmarks/results/`.
  Defaults to auto-*declining* any high-risk action mid-benchmark (a
  benchmark runs unattended; auto-approving a real send/delete/purchase
  unattended is exactly the failure mode the safety guardrails exist to
  prevent) -- pass `--unsafe-auto-approve-high-risk` only for tasks built to
  be safe to fully automate. Harness logic (task loading, pass/fail
  aggregation) is tested directly; actually running it against a live
  backend hasn't been done yet.
- **Real automated tests.** `tests/` (new) -- 75 tests covering the safety
  checks, coordinate scaling, screen-diff functions, schema validation, the
  stuck-repeat detector, the retrospective-mention filter below, and three
  full `run_agent_loop()` integration tests (stuck-repeat nudge reaches the
  model's prompt exactly when it should; `risk_level` reaches the
  confirmation gate even with no trigger word in `reasoning`; a real failed
  run's exact reasoning text no longer triggers a confirmation cascade).
  Run with `python -m unittest discover -s tests`. Uses fake `pyautogui`/
  `mss` modules (`tests/_fakes.py`) since this logic doesn't need a real
  screen -- the GUI-driving code itself (`pyautogui.click`, Windows UI
  Automation calls) is still unverified against a real machine.
- **Stricter, targeted stuck-detection.** `_find_stuck_repeat()` in
  `core/loop.py` fires after just TWO repeats of the exact same action
  against the exact same target (not three generic no-change results across
  possibly-different targets), and the nudge it sends is specific to what
  failed -- e.g. a repeated `focus_window` failure now says "click directly
  instead," where the old generic nudge only ever suggested `open_url`/
  `launch_app`, which doesn't even apply to a focus problem. Directly
  modeled on the real "open Notepad" run where `focus_window` was retried 4
  times before the model gave up on its own.
- **Fixed: confirmation-fatigue cascade in the keyword check.** A real
  "send an email" run's reasoning mentioned "clicking 'Send' didn't work"
  once while explaining a retry, and every later, unrelated action for the
  rest of the run -- retyping body text, clicking a close button, clicking
  Compose again -- got flagged high-risk too, purely from that leftover
  mention: 7 straight unnecessary confirmations before the human declined
  out of fatigue and the run was abandoned without ever sending anything.
  `contains_high_risk_keyword()` now filters out sentences that read as
  narrating a past attempt (`RETROSPECTIVE_MARKERS` in `config.py`) before
  matching. Verified against the real run's exact reasoning text: matches
  dropped from 8 steps to the 2 genuine send clicks (`tests/test_retrospective_filter.py`).
  Known limit, documented and tested rather than hidden: a single sentence
  that mixes a past-tense reference with a genuinely current risky action
  (e.g. "since deleting it last time failed, I will delete it again now")
  can still slip through this heuristic -- `risk_level`, set independently
  by the model rather than derived from parsing this text, is the intended
  backstop for that case.
- **Fixed: `open_url`/`launch_app` not stealing foreground focus.** A real
  "search the weather" run showed the search actually succeeding in a
  background browser tab while a different app (a dev environment) stayed
  focused; the next screenshot was accurate but simply didn't show the
  result yet, so the model concluded the action had failed and launched a
  second, redundant Chrome window. Root cause: both actions call
  `webbrowser.open()` / `subprocess.Popen(["cmd", "/c", "start", ...])` and
  trust the OS to hand over focus, which it does not always do.
  `_bring_forward_after_open()` in `actions/executor.py` waits briefly then
  reuses `focus_window()`'s own verified match-and-raise logic against a
  short list of hints (`COMMON_BROWSER_WINDOW_HINTS` for `open_url`, the app
  name itself for `launch_app`), appending a bracketed confirmation like
  `[brought 'chrome' window to the foreground]` to the outcome string when
  it succeeds, and staying silent (never claiming a false success) when it
  can't confirm anything. Covered by `tests/test_bring_forward.py` (6
  tests: non-Windows short-circuit, first-hint-wins, falls through to the
  second hint, and -- the actual bug -- never reports success when nothing
  was confirmed).

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
- `focus_window` reported success even when it silently did nothing. A real
  "open Notepad and type a note" run found the window correctly every time
  but Windows' foreground-lock protection blocked the actual switch -- the
  code already used the standard `AttachThreadInput` workaround, but never
  checked whether it had actually worked, so it kept returning `"focused
  window: 'Untitled - Notepad'"` on 4 straight attempts (steps 2, 4, 5, 6)
  while the screen never changed, until the model gave up and clicked the
  title bar directly. Fixed: `focus_window` now verifies the real foreground
  window afterward with `GetForegroundWindow()`, retries once with a
  simulated Alt keypress (Windows relaxes the lock right after real input) if
  the first attempt didn't take, and returns an honest failure message
  instead of a false "focused" result if it still didn't work. Not yet
  verified against a real Windows run -- next real test should confirm this
  removes the repeated-focus_window pattern.
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
