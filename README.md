# AI Screenshot Helper

A small Windows 10/11 utility: press **Ctrl+Shift+S**, select a screen region, and get an AI answer in a floating window. Python 3.11+ required.

## Features

- Native Windows global hotkey, configurable in Settings.
- Drag selection on any display, bright selection preview, Esc cancellation.
- In-memory PNG capture with display scaling correction and optional downscaling.
- Background vision requests with loading state and readable errors.
- Ask, Debug, Extract, Explain, and Translate modes.
- Ask conversation, structured diagnostics, and extracted tables/code/fields with contextual copy buttons.
- Tray menu, configurable always-on-top behavior, and JSON settings.

## Demo

### Phase 1 workflows

- **Ask:** free-form answers with an inline follow-up field. The last six conversation turns stay in memory for the current screenshot. Retry starts a fresh conversation; replacing or closing the screenshot clears it.
- **Debug:** separate Detected Problem, Evidence, Likely Cause, Suggested Fix, and Confidence sections. Copy Error and (when a fix exists) Copy Fix copy locally. Ask AI and Explain Why continue in Ask with the diagnostic as context.
- **Extract:** identifies text, code, table, receipt, contact, event, assignment, JSON, or URL. Tables use a grid with CSV/JSON/Markdown copy actions; code uses a monospace view and Copy Code; other types show extracted fields. Nothing is executed or sent to another app by copy actions.
- **Explain / Translate:** retain their existing text workflows for later phases.

Malformed structured answers show a retryable error instead of guessed data. Old General and Summarize settings migrate to Ask; Extract Text migrates to Extract. Smart classification and external integrations are deferred.

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
The project depends only on PySide6, python-dotenv, and the official OpenAI SDK.

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
| `src/actions.py` | Local clipboard representations (CSV, JSON, Markdown, code, fixes) |
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

Next validate these three workflows with representative screenshots on the configured vision model. Then add Phase 2's independent Smart classifier/router for assignment, event, code_error, table, and unknown, with a confidence threshold and Ask fallback. Spatial explanations and translation overlays come later.
