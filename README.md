# SnapFlow

SnapFlow is a Windows desktop app for turning a selected part of your screen into an AI answer or structured information. Review the result, then choose whether to copy it, save it, or use it in a workflow.

Capture and provider requests start with a user action. Workflow actions that write to connected services require confirmation.

## What you can do

- **Capture and ask:** Use the global shortcut to select a screen region. Ask a question or choose Smart, Debug, Extract, Explain, or Translate.
- **Extract structured information:** Smart mode recognizes supported content such as events, assignments, code errors, and tables. Review the fields before copying or exporting them.
- **Create Visual Skills:** Use built-in skills or define custom screenshot types, fields, and reusable actions without writing code. Test and edit a skill before enabling it.
- **Build Visual Workflows:** Combine a skill match, conditions, and ordered actions. Suggest mode lets you review a proposed run. Auto mode still asks you to confirm before actions run.
- **Connect services:** Workflows can create Google Calendar events, append Google Sheets rows, or create Todoist tasks. Local actions include copying results and saving calendar files.
- **Manage examples and data:** Save verified examples when you choose, review skill versions, evaluate changes, back up and restore local data, and export a sanitized support bundle when you request one.
- **Check usage and updates:** View token usage and estimated costs when model rates are known. Optional update checks show release information; they do not download or install updates.

## Get started

SnapFlow targets Windows 10 and 11 on x64. Packaged releases are available as an installer or a complete portable ZIP when provided; packaged users do not need Python.

To run from source, install Python 3.11 or later, then open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The app starts in the system tray. Choose **Capture Screenshot** or press **Ctrl+Shift+S**, select a region, and review the result. Press **Esc** to cancel a selection. Choose **Quit** from the tray menu to exit.

To preview screen capture without making a provider request:

```powershell
python main.py --preview
```

## Configure an AI provider

Use the first-run setup or **Settings** to select a model and provider endpoint. Saved API keys are stored in Windows Credential Manager. For source development, you can instead copy `.env.example` to `.env` and set values there:

```powershell
Copy-Item .env.example .env
notepad .env
```

| Setting | Purpose |
| --- | --- |
| `AI_API_KEY` | API key for your provider |
| `AI_MODEL` | Vision-capable model name |
| `AI_BASE_URL` | OpenAI-compatible API endpoint |
| `AI_IMAGE_DETAIL` | Optional image detail setting supported by the provider |
| `AI_TIMEOUT` | Request timeout in seconds, up to 600 |
| `AI_MAX_TOKENS` | Maximum response length |

The default endpoint is OpenAI, with `gpt-4.1-mini` as the default model. Other OpenAI-compatible providers must support image input. An `AI_API_KEY` environment value takes precedence over a saved key. Provider and model choices saved in Settings take precedence over `AI_BASE_URL` and `AI_MODEL` from `.env`.

Each request sends the selected image to the configured provider and may incur a charge. Testing a connection sends a short text request and may also incur a charge.

## Privacy and local data

- Captures are held in memory and are not kept as screenshot history. A screenshot is saved only if you choose to save it with a verified example.
- Asking a follow-up or retrying may send the retained screenshot to the provider again.
- SnapFlow does not run local OCR. The configured AI provider processes images sent with your requests.
- Saved keys are kept in Windows Credential Manager, not in `config.json` or backup archives. Provider and model settings are non-secret values.
- Settings and user data are stored under `%APPDATA%\SnapFlow`. The source-only `.env` file stays in the project folder.
- Workflow writes to Google Calendar, Google Sheets, or Todoist happen only after you confirm the action. Imported workflows start disabled in Suggest mode.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Shift+S | Start screen capture |
| Esc | Cancel screen selection |

Change the capture shortcut in Settings. If another app already uses it, SnapFlow reports the conflict and tray capture remains available.

## Development checks

Run the offline test suite, Python compilation check, and application smoke check from the project folder:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py src tests
python main.py --smoke-test
```

## Limitations

- Screen selection is limited to one display. Protected content, secure desktops, and some exclusive full-screen applications may not be capturable.
- AI output can be incomplete or incorrect. Review extracted values and suggested actions before relying on them.
- Requests do not stream or retry automatically. Closing a result prevents it from appearing later but cannot retract a request already sent to a provider.
- SnapFlow does not start automatically with Windows or install updates automatically.
