# SnapFlow

SnapFlow is a Windows desktop assistant that turns selected screen content into AI answers, structured information, and reusable workflows. Capture a region, ask a question, and review the result before copying, saving, or taking action.

The app runs in the system tray and supports cloud AI providers, compatible local models, and optional connections to other services.

## Features

- **Capture and ask:** Select part of your screen and ask a question, or choose Smart, Debug, Extract, Explain, or Translate.
- **Structured extraction:** Turn supported content such as events, assignments, code errors, and tables into fields you can review and export.
- **Visual Skills:** Use built-in skills or define custom screenshot types, extraction fields, and reusable actions without writing code.
- **Visual Workflows:** Combine skill matches, conditions, and ordered actions. Review proposed runs and confirm connected-service writes.
- **Visual Memory:** Save selected results, search them locally, and optionally use retrieved text in AI questions.
- **Contextual assistance:** Ask follow-up questions using the current analysis, selected memories, and permitted desktop context.
- **Connected services:** Create Google Calendar events, export events as `.ics` files, append Google Sheets rows, and create Todoist tasks.
- **MCP extensions:** Connect local stdio or Streamable HTTP servers to use reviewed tools, resources, and prompts. An optional SnapFlow MCP server supports explicitly shared capabilities.
- **Local and cloud AI:** Choose an OpenAI-compatible cloud provider or local runtime, configure routing, and control cloud fallback.
- **Data management:** Review saved examples and skill versions, evaluate changes, back up local data, and export sanitized support information.

## Requirements

- Windows 10 or 11, x64.
- Python 3.11 or later when running from source. Packaged builds do not require Python.
- A vision-capable AI provider or compatible local vision model for screenshot analysis.

Cloud features require an internet connection and provider credentials. Local inference requires a separately configured runtime and model.

## Getting started

If using a packaged build, launch SnapFlow from the installed app or extracted portable folder.

To run from source, open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Use the first-run setup or **Settings** to configure your AI provider. Then:

1. Press **Ctrl+Shift+S** or choose **Capture Screenshot** from the tray menu.
2. Select the screen region you want to analyze. Press **Esc** to cancel.
3. Choose a mode or enter a question.
4. Review the answer, then copy it, save it to Memory, or choose an available action.

The capture shortcut can be changed in Settings. Choose **Quit SnapFlow** from the tray menu to exit.

To preview screen capture without making a provider request:

```powershell
.\.venv\Scripts\python.exe main.py --preview
```

## AI configuration

Configure the provider endpoint, model, and API key in **Settings**. Saved API keys use Windows Credential Manager.

For source development, you can also copy `.env.example` to `.env` and edit its values:

```powershell
Copy-Item .env.example .env
notepad .env
```

| Variable | Purpose |
| --- | --- |
| `AI_API_KEY` | Provider API key |
| `AI_MODEL` | Vision-capable model identifier |
| `AI_BASE_URL` | OpenAI-compatible API endpoint |
| `AI_IMAGE_DETAIL` | Optional image detail setting supported by the provider |
| `AI_TIMEOUT` | Request timeout in seconds, up to 600 |
| `AI_MAX_TOKENS` | Maximum response length |

An `AI_API_KEY` environment value takes precedence over a saved key. Provider and model choices saved in Settings take precedence over `AI_BASE_URL` and `AI_MODEL` from `.env`.

Cloud requests send selected content to the configured provider and may incur usage charges. Connection tests can also make provider requests.

### Local models and routing

Open **Settings → AI & Models** to configure a running OpenAI-compatible loopback server. Set its model identifier and capabilities, then use the explicit text, vision, and JSON checks to verify support. Screenshot analysis requires a working vision model.

Available routing choices include **Local Only**, **Prefer Local**, **Automatic**, **Cloud Only**, and **Ask Every Time**. Cloud fallback must be enabled before supported local-first routes can use it.

The optional managed GGUF runtime requires separately obtained model weights and a compatible `llama-server.exe`. Vision models also need a matching projector. SnapFlow does not automatically download model weights or install runtimes.

## Optional capabilities

### Agent Missions and desktop assistance

Enable **Agent Missions** in Beta Features to turn a goal into a plan you can review and execute. **Plan Only** creates a plan without running its steps. Writes require specific approval.

Desktop Context can supply permitted window metadata, bounded Windows UI Automation content, and selected text. Clipboard access requires explicit consent and is disabled by default.

**Controlled Desktop Actions** is an opt-in beta for reviewed semantic Windows UI Automation actions within a selected window. Actions require previews, approval, and verification.

**Demonstration Automation** lets you teach a selected-window task through **Automations → Desktop Skills**. Review the recorded steps, choose inputs, dry run, and approve a test before publishing a local skill version. Enable it in Beta Features before use.

### Browser Companion

The optional Chrome and Edge companion provides read-only current-tab assistance, source review, and sidebar chat through SnapFlow. Configure it under **Settings → Privacy & Permissions → Browser Companion**. It requires the companion extension, Native Messaging host, and an enabled desktop bridge. Browser actions and browser Teach are not available in this preview.

### Service connections

Connect supported services in the app before using their actions. Google sign-in for source runs can use `SNAPFLOW_GOOGLE_CLIENT_ID` and `SNAPFLOW_GOOGLE_CLIENT_SECRET` in `.env` for a Desktop OAuth client.

MCP connections support local stdio and Streamable HTTP, with bearer or OAuth authentication where supported. Review exposed tools and shared context before enabling a connection.

## Privacy and local data

Captures begin with a user action. Screenshots are held in memory and are saved only when you explicitly include them in a verified example or Visual Memory record. Follow-up questions and retries may send the retained screenshot to the selected provider again.

Settings and user data live under `%APPDATA%\SnapFlow`. Visual Memory uses a local database, and keyword search works without a provider connection. AI Memory Questions are optional and send retrieved saved text to the configured provider.

Imported model assets live under `%LOCALAPPDATA%\SnapFlow`. Backups exclude credentials, model weights, and runtime binaries.

**Private Mode** blocks application-owned external traffic, including cloud AI and remote integrations. **Local Only** restricts inference while allowing other authorized integrations to use the network. Third-party runtimes and stdio programs manage their own network access.

Local reliability records and support exports can help diagnose problems. SnapFlow does not implement remote analytics or automatic crash uploads.

## Troubleshooting

- **Capture shortcut does not work:** Check for a shortcut conflict in Settings, or capture from the tray menu.
- **Screenshot analysis fails:** Check the endpoint, credentials, model identifier, and image-input support. For local models, run the vision check in AI & Models.
- **The app cannot start normally:** Try Safe Mode:

  ```powershell
  .\.venv\Scripts\python.exe main.py --safe-mode
  ```

- **A task was interrupted:** Review its recovery status before retrying. Attempted effects are not automatically replayed.

## Development

With dependencies installed, run these checks from the project folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q main.py src tests
.\.venv\Scripts\python.exe main.py --smoke-test
```

## Limitations

- Screen selection is limited to one display. Protected content, secure desktops, and some exclusive full-screen applications may not be capturable.
- AI output can be incomplete or incorrect. Review extracted information and proposed actions before using them.
- Local model compatibility and performance depend on the runtime, model, and hardware.
- Agent Missions, Controlled Desktop Actions, and Demonstration Automation are optional beta capabilities. Browser Companion is a read-only preview.
- SnapFlow does not start automatically with Windows or install updates automatically.
