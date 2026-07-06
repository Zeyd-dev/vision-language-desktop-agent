# Vision-Language Desktop Agent

An autonomous agent that takes a natural-language instruction, observes your
screen, and completes the task by controlling the mouse and keyboard — one
action at a time, in a loop, until it's done or it gives up safely.

```
screenshot -> Claude (perceive + decide) -> execute one action -> repeat
```

## Project layout

```
agent.py            CLI entry point + the main loop controller
config.py           All tunables (model, caps, safety keywords, paths)
run_logger.py        Writes runs/<timestamp>/ logs for after-the-fact review
backends/
  base.py           VLMBackend interface + AgentAction schema (model-agnostic)
  claude_backend.py Claude implementation, uses tool-use to force valid JSON
  prompts.py        The system prompt given to the model
actions/
  executor.py       mss screenshot capture + pyautogui execution
  safety.py         High-risk keyword detection, confirmation prompts, kill switch
runs/               Created automatically; one folder per run
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

3. **Set your Anthropic API key.** Copy `.env.example` to `.env` and fill in
   your key:

   ```
   copy .env.example .env
   ```

   Then edit `.env` and set `ANTHROPIC_API_KEY=sk-ant-...`. Get a key at
   https://console.anthropic.com/.

4. **Display scaling note:** if your display is set to anything other than
   100% scaling (Settings > System > Display > Scale), clicks can land in
   the wrong place because pyautogui and screenshots may disagree about
   pixel coordinates. `actions/executor.py` calls Windows' DPI-awareness API
   on startup to fix this automatically for most setups. If clicks still
   seem consistently offset, try setting scaling to 100% as a workaround,
   or check `Screenshot.scale` in a test run's logs to see the mismatch.

5. **Antivirus/UAC:** pyautogui simulates real mouse/keyboard input. Some
   antivirus software flags this behavior — if the agent's clicks don't
   register, check whether your AV is silently blocking synthetic input.

## Usage

```
python agent.py "Open a browser and go to YouTube"
```

Optional flags:

```
python agent.py "your task" --max-iterations 15 --max-minutes 5
```

While it's running:
- **Kill switch:** type `stop` and press Enter in the terminal at any time.
  The agent checks for this before every action and while any confirmation
  prompt is on screen.
- **Physical failsafe:** slam the mouse into any screen corner. pyautogui
  aborts immediately (`pyautogui.FAILSAFE = True`).
- **High-risk confirmations:** if the model's own reasoning (or the text
  it's about to type) contains a word like "send", "delete", "purchase",
  "submit payment", "sign out", etc. (see `HIGH_RISK_KEYWORDS` in
  `config.py`), the agent pauses and asks you to type `y` before executing
  that specific action.
- **Periodic check-ins:** every 15 iterations (configurable via
  `CHECKIN_EVERY_N_ITERATIONS`), the agent stops and asks whether to keep
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
`SCREENSHOT_MAX_WIDTH` in `config.py`) before being sent to Claude, to keep
image tokens and latency down. The model's `coordinates` are always
relative to the *downscaled* image it was shown. `Screenshot.to_real_coords()`
rescales them back to real screen pixels before `pyautogui` executes the
click, so you never need to think about this — but it's why the run log
stores full-resolution screenshots (for you to inspect) separately from
the smaller ones actually sent to the API.

## Extending

- **Swap models:** implement `VLMBackend.decide()` (see `backends/base.py`)
  for another provider and pass it into `Agent`/`run()` in place of
  `ClaudeBackend`.
- **Add a reflect/replan step:** `AgentAction` already carries an optional
  `expected_outcome` field the model fills in each turn. A future version
  can capture the *next* screenshot, compare it against that expectation
  (e.g. via another model call), and explicitly branch on "this didn't do
  what I expected" instead of always trusting the next `decide()` call to
  notice on its own.

## Known limitations (MVP)

- Single monitor only (primary display).
- No OCR/accessibility-tree grounding — click accuracy depends entirely on
  the model's pixel estimate from the screenshot.
- The high-risk keyword list is a simple substring match on the model's own
  reasoning text, not a semantic understanding of risk — treat it as a
  speed bump, not a guarantee.
