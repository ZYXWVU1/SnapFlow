# AI Screenshot Helper

A small Windows 10/11 utility: press **Ctrl+Shift+S**, select a screen region, and get an AI answer in a floating window. Python 3.11+ required.

## Features

- Native Windows global hotkey, configurable in Settings.
- Drag selection on any display, bright selection preview, Esc cancellation.
- In-memory PNG capture with display scaling correction and optional downscaling.
- Background vision requests with loading state and readable errors.
- Smart, Ask, Debug, Extract, Explain, and Translate modes.
- Ask conversation, structured diagnostics, and extracted tables/code/fields with contextual copy buttons.
- Tray menu, configurable always-on-top behavior, and JSON settings.

## Demo

### Phase 1 workflows

- **Ask:** free-form answers with an inline follow-up field. The last six conversation turns stay in memory for the current screenshot. Retry starts a fresh conversation; replacing or closing the screenshot clears it.
- **Debug:** separate Detected Problem, Evidence, Likely Cause, Suggested Fix, and Confidence sections. Copy Error and (when a fix exists) Copy Fix copy locally. Ask AI and Explain Why continue in Ask with the diagnostic as context.
- **Extract:** identifies text, code, table, receipt, contact, event, assignment, JSON, or URL. Tables use a grid with CSV/JSON/Markdown copy actions; code uses a monospace view and Copy Code; other types show extracted fields. Nothing is executed or sent to another app by copy actions.
- **Explain / Translate:** retain their existing text workflows for later phases.

Malformed structured answers show a retryable error instead of guessed data. Old General and Summarize settings migrate to Ask; Extract Text migrates to Extract. External integrations remain deferred.

### Smart mode - Phase 3

AI Screenshot Helper automatically detects supported screenshot types and turns them into structured actions.
Choose **Smart**, capture a region, review the extracted details, then choose an action.

| Smart Skill | Structured information | Local actions |
| --- | --- | --- |
| Assignment | Course, title, deadline, points, instructions, submission | Calendar file, details, Markdown, Ask AI |
| Event | Date/time, location, meeting link, organizer | Calendar file, meeting link, details, Ask AI |
| Code Error | Problem, evidence, likely cause, suggested fixes | Copy error/fix/diagnosis, Explain, Ask AI |
| Table | Headers, rows, notes | CSV, JSON, Markdown, tab-separated text, Ask AI |
| Unknown or uncertain | Ask response | Existing conversation workflow |

```text
Screenshot -> Classifier -> Smart Router -> Skill Registry -> Skill
    -> Parse -> Validate -> Normalize -> SkillResult -> Dynamic UI -> Action Registry
```

Smart uses two background AI requests: classification and skill extraction (or Ask fallback).
Classification confidence must meet `smart_classification_threshold` (default `0.75`).
Confidence is a model estimate. Missing fields are hidden; malformed JSON offers Retry and Ask AI.
Closing a result invalidates late replies. Ask AI retains the original screenshot and validated structured data.
Manual Ask, Debug, Extract, Explain and Translate remain available.

Copy actions use the system clipboard. Calendar export opens a save dialog and writes a local `.ics` file;
it never opens URLs or connects to calendar accounts. Missing dates disable calendar export; missing times
produce all-day events. Dates without a clear year remain empty. Missing timezones use floating local time
with an on-screen note. UTC, explicit offsets and IANA zones are supported; ambiguous abbreviations and
DST transition times require clearer source data. End times at/before start are omitted with a warning.
Table previews show at most 10 rows, while exports include all rows.

Settings is owned by the result window and brought to the foreground on every open, including when results
are set to always stay on top.

Offline and optional live evaluation:

```powershell
python -m scripts.generate_smart_samples
python -m scripts.evaluate_skills
python -m scripts.evaluate_skills --live --limit-per-category 1
python -m tests.render_phase3
```

The default evaluation only inventories local samples. `--live` sends images to your provider and may incur
charges. Add `01.expected.json` beside `01.png` with expected normalized fields (including missing-field nulls)
to score factual extraction, not just JSON validity. Logs/metrics exclude extracted content and passcodes.
Review real screenshots for correctness, completeness and invented fields before relying on the results.

`Ctrl+Shift+S → drag over a paragraph/code/error → release → answer → Copy`

To try capture without an API key or network request:

```powershell
python main.py --preview
```

This displays a thumbnail of the selected region. No screenshots are written to disk.

## Installation

Open PowerShell in this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If activation is unavailable, use `.\.venv\Scripts\python.exe` in place of `python`.
The project uses PySide6, python-dotenv, the official OpenAI SDK, and tzdata for Windows calendar timezone support.

When started with `python main.py`, the app checks for these packages first. If one
is missing, it automatically runs pip with the same Python interpreter and installs
the requirements for the current user (or into the active virtual environment).
Internet access and pip are required for this first-run setup. For a more reliable
end-user experience, distribute a packaged executable or pre-created virtual
environment instead of relying on runtime installation.

## Setup API Key

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `AI_API_KEY` to your API key. `AI_MODEL` defaults to `gpt-4.1-mini`; choose a vision-capable model available to your API account. `AI_BASE_URL` defaults to `https://api.openai.com/v1`. A different service must support the same Chat Completions image request format.

For SiliconFlow, use `https://api.siliconflow.cn/v1` and a vision model available in your SiliconFlow account, for example `Qwen/Qwen2.5-VL-72B-Instruct`. Set `AI_IMAGE_DETAIL=low` for smaller screenshot requests. SiliconFlow model availability can change, so copy the exact model ID from its model list.

Alternatively, enter a key through tray → Settings. That key lasts only for the current process and is never written to `config.json`. Environment variables take precedence over `.env`; restart after editing `.env`.

The client sends PNG data using the [documented image input format](https://developers.openai.com/api/docs/guides/images-vision). Each selection, mode change with a retained screenshot, or Ask Again sends a new request to the configured provider and may incur API charges. There is no local OCR stage or screenshot history. `.env` and `config.json` are ignored by Git.

## Running

```powershell
python main.py
```

The application starts in the system tray (check Windows' hidden icons). Double-click its icon or choose **Capture Screenshot**. Closing the popup keeps the app running. Choose **Quit** in the tray menu to exit. If a request is active, Quit waits for it to finish or time out without freezing the GUI.

For this workspace, a portable test runtime is also available:

```powershell
..\.python-runtime\python.exe main.py
```

## Keyboard Shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Shift+S | Start capture from any application |
| Esc | Cancel selection without an AI request |

Settings supports combinations of Ctrl, Alt, Shift, Win and a letter or number. Hotkey conflicts produce an error; tray capture stays available. The source plan included both S and A: this implementation consistently defaults to S. Set `ctrl+shift+a` in Settings if preferred.

## Project Architecture

| File | Responsibility |
| --- | --- |
| `main.py` | Qt entry point |
| `src/app.py` | Tray lifecycle, capture coordination, thread-pool worker |
| `src/config.py` | Validated JSON settings and hotkey parsing |
| `src/hotkeys.py` | Windows RegisterHotKey / Qt native events |
| `src/screenshot.py` | Screen snapshots, logical-to-physical crop, resize, PNG encoding |
| `src/llm_client.py` | Provider-specific requests, credentials, error mapping |
| `src/prompts.py` | Central mode labels and prompts |
| `src/modes.py` | Debug/Extract contracts, result model, and JSON validation |
| `src/actions.py` | Shared CSV/Markdown and manual mode serializers |
| `src/skills/` | Skill contracts, registry, four schemas/prompts, normalization and extraction worker |
| `src/skill_actions.py` | Action definitions, availability and payload generation |
| `src/calendar_export.py` | Local iCalendar serialization, escaping, folding and timezone conversion |
| `src/ui/skill_view.py` | Skill-driven fields, diagnostics and bounded table previews |
| `src/ui/skill_actions.py` | User-triggered clipboard, save dialog and follow-up adapter |
| `src/ui/structured_result.py` | Diagnostic sections and extracted tables/code/fields |
| `src/ui/` | Selection, results, settings widgets |
| `tests/` | Offline unit and Qt integration tests |

Settings are saved atomically to `config.json` beside `main.py`. Invalid configuration shows an error and uses defaults for the session; it is only replaced when Settings is saved. Screenshots are taken before the overlays appear so the tint and border are excluded. Captures and requests run one at a time. Close discards the retained image and ignores late responses.

## Validation

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py src tests
python main.py --smoke-test
```

The smoke check starts the real application, registers the hotkey and tray, then exits. Tests cover prompt/config validation, scaled crop and PNG round trip, API payload/error handling, native hotkey dispatch and conflicts, mouse selection, Esc, background response delivery, Copy, and late-result suppression.

Manual acceptance checklist:

1. Run `--preview`; capture a known rectangle on each display at its normal scaling. Confirm correct pixels and Esc cancellation.
2. Configure a real key and capture a paragraph, Python code, an error, and a table. Compare Ask, Debug, and Extract on the same screenshot; check Explain and Translate too.
3. Try follow-up questions, Retry, and contextual copy actions. Close while analyzing; confirm the popup stays closed.
4. Change the hotkey, restart, and confirm persistence. Test a shortcut already used by another app.
5. Disconnect the network and check the readable error. Quit during a request.

## Limitations

- Selection stays within one display; cross-monitor drags are clipped to the starting display.
- Protected content, secure desktops, and some exclusive full-screen applications may not be capturable.
- Requests use a 45-second network timeout and no automatic retries. Closing a result suppresses the answer but cannot retract a submitted request. A new capture waits for the active request to finish.
- Ask follow-ups resend the original image with up to six previous turns. This context is kept only in memory.
- JSON structure is validated locally; factual accuracy and confidence remain dependent on the configured vision model. Large responses may exceed AI_MAX_TOKENS and require a smaller capture or a higher configured limit.
- No streaming, installer, auto-start, or automatic updates in this MVP.
- Automated tests use a mocked AI service. Real model recognition and physical multi-monitor/hotkey interaction still require the manual checks above.

## Roadmap

Validate the four Smart skills against representative screenshots with expected-field fixtures. A possible
Phase 4 is a user-reviewed destination integration, with explicit permissions and confirmation before sending
data. No accounts, cloud sync, custom skills or autonomous actions are included in Phase 3.
