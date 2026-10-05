# SnapFlow

Phase 13 adds local reliability records, crash recovery and Safe Mode, performance timings, local beta features, feedback exports, Health Center, and release gates. The Windows candidate is `0.8.0-beta.2`; actual qualification and remaining blockers are recorded in [the Phase 13 report](docs/PHASE13_IMPLEMENTATION.md).

Start with [the beta testing guide](docs/BETA_TESTING_GUIDE.md), [privacy and local analytics](docs/PRIVACY_ANALYTICS.md), [recovery guidance](docs/CRASH_RECOVERY.md), [known issues](docs/BETA_KNOWN_ISSUES.md), and [the release process](docs/RELEASE_PROCESS.md). Remote analytics are not implemented. Genuine local model inference and live providers require separate qualification; synthetic checks are identified in the report.

SnapFlow is a Windows desktop app for turning a selected part of your screen into an AI answer or structured information. Review the result, then choose whether to copy it, save it, or use it in a workflow.

Capture and provider requests start with a user action. Workflow actions that write to connected services require confirmation.

## What you can do

- **Capture and ask:** Use the global shortcut to select a screen region. Ask a question or choose Smart, Debug, Extract, Explain, or Translate.
- **Extract structured information:** Smart mode recognizes supported content such as events, assignments, code errors, and tables. Review the fields before copying or exporting them.
- **Create Visual Skills:** Use built-in skills or define custom screenshot types, fields, and reusable actions without writing code. Test and edit a skill before enabling it.
- **Build Visual Workflows:** Combine a skill match, conditions, and ordered actions. Suggest mode lets you review a proposed run. Auto mode still asks you to confirm before actions run.
- **Connect services:** From a detected Event result, add the event to Google Calendar with one click or save it as an `.ics` file. Workflows can also create Google Calendar events, append Google Sheets rows, or create Todoist tasks.
- **Connect MCP extensions:** Use local stdio or Streamable HTTP with bearer/OAuth authentication. Review tools, run them in manual Workflows, and select resources/templates/prompts for Context. The optional SnapFlow server uses a local stdio bridge with explicit sharing and native Workflow approval.
- **Manage examples and data:** Save verified examples when you choose, review skill versions, evaluate changes, back up and restore local data, and export a sanitized support bundle when you request one.
- **Build Visual Memory:** Choose **Save to Memory** on a structured Skill or Extract result, then search saved information locally from the Memory page. AI Memory Questions are an optional setting and send only retrieved saved text to your provider.
- **Ask with Context:** After reviewing a screenshot result, ask a follow-up using the current analysis, memories you select, or a separately enabled session-scoped Memory search. Source buttons open the saved records used in the answer.
- **Check usage and updates:** View token usage and estimated costs when model rates are known. Optional update checks show release information; they do not download or install updates.
- **Choose where AI runs:** Keep the existing cloud provider, configure a loopback local server, or explicitly start an optional managed GGUF runtime. Private Mode blocks application-owned external traffic; Local Only restricts inference.
- **Compare models and search Memory locally:** Explicitly compare local/cloud models on a saved dataset, or configure local text embeddings for background Memory indexing and semantic search with keyword fallback.

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

In Cloud Only, requests send selected content to the configured provider and may incur a charge. Testing the cloud connection sends a short text request and may also incur a charge. Existing installations keep Cloud Only; cloud fallback is off by default.

## Local AI and Private Mode

Open **Settings → AI & Models**. Enter a running OpenAI-compatible loopback endpoint, its model ID, and the actual capabilities/context length. Use **Check Runtime**, **List Server Models**, **Test Text**, **Test Vision**, and **Test JSON** explicitly. Local execution needs no cloud API key; screenshot analysis requires a working Vision model.

Choose **Local Only**, **Prefer Local**, **Automatic**, **Cloud Only**, or **Ask Every Time**, then save. Prefer Local and Automatic can use cloud only when fallback is enabled and privacy permits it. Private Mode overrides those choices and blocks application-owned external AI, remote integrations/MCP/OAuth and outbound checks. Local Only limits inference while other authorized integrations can still use the network. Loopback servers and approved stdio executables retain their own networking behavior; Private Mode is not a firewall for third-party programs.

The optional **Import GGUF** / **Start Selected Model** flow needs separately obtained weights, an existing compatible `llama-server.exe`, and a matching projector for Vision. Managed startup uses loopback authentication, offline flags and CPU execution. No model catalog, download, runtime distribution, preload or GPU installation occurs automatically. The backend can import inert standalone ONNX data; the UI and managed inference support GGUF. Detected GPU/NPU information does not establish acceleration support.

**Evaluation → Compare AI Models** runs only after confirming saved cases, exact model revisions and potential cloud charges. Automatic routing reuses measured custom-Skill evidence only when the current definition, dataset/revision, configured model revisions and local hardware match, with at least five cases and at most 30 days of age.

For semantic Memory retrieval, configure **Local Embeddings** in AI & Models, then select **Rebuild Memory Search Index** in Settings. Model/runtime/revision/endpoint/dimension identities keep vectors separate. Rebuilds run incrementally in a worker and can be cancelled; Memory semantic queries run in the background, with FTS keyword fallback.

Read [local setup](docs/ai/LOCAL_MODELS.md), [Private Mode](docs/ai/PRIVATE_MODE.md), [routing](docs/ai/HYBRID_ROUTING.md), [evaluation and embeddings](docs/ai/MODEL_EVALUATION.md), and the [runtime architecture](docs/ai/AI_RUNTIME_ARCHITECTURE.md).

## Google sign-in configuration

Google sign-in uses the app's Desktop OAuth client ID. If Google's token endpoint requires the matching client secret, source runs read `SNAPFLOW_GOOGLE_CLIENT_SECRET` from `.env`; Windows bundles include credentials supplied through the build process environment. An existing installation may continue using its saved legacy Google client ID.

## Privacy and local data

- Captures are held in memory and are not kept as screenshot history. A screenshot is saved only if you choose to include it with a verified example or Visual Memory record.
- Visual Memory records live in `%APPDATA%\SnapFlow\learning.sqlite3`; optional images live under `%APPDATA%\SnapFlow\memory\images`. Local keyword search needs no provider connection. AI Memory Questions are off by default.
- Contextual Assistant sessions start with the current screenshot analysis only. Selecting a Memory or enabling session search sends bounded saved text, not saved images, to the configured provider when you press Ask. Context sessions are temporary and are cleared when the screenshot result closes.
- Asking a follow-up or retrying may send the retained screenshot to the provider again.
- SnapFlow does not embed a local OCR engine. The selected local Vision runtime or cloud provider processes images sent with your requests.
- Saved keys are kept in Windows Credential Manager, not in `config.json` or backup archives. Provider and model settings are non-secret values.
- Settings and user data are stored under `%APPDATA%\SnapFlow`. The source-only `.env` file stays in the project folder.
- Imported weights/projectors use `%LOCALAPPDATA%\SnapFlow\models`; optional runtimes use `%LOCALAPPDATA%\SnapFlow\runtimes`. Backups include the small `local_models.json` registry, but exclude weights, runtime binaries and credentials. Restored paths may need re-importing on another computer. Installer uninstall behavior is unchanged; optional local assets require manual cleanup if you want to remove them.
- AI execution History stores runtime/model identity, timing and reasons without prompts, responses, screenshots or extracted values. Existing saved examples, Memory records and evaluation reports retain their separate save/delete policies.
- Direct Google Calendar event creation happens only after you click **Add to Google Calendar**. Workflow writes to Google Calendar, Google Sheets, or Todoist happen only after you confirm the action. Imported workflows start disabled in Suggest mode.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Shift+S | Start screen capture |
| Esc | Cancel screen selection |

Change the capture shortcut in Settings. If another app already uses it, SnapFlow reports the conflict and tray capture remains available.

## Development checks

Phase 13 has started with the reliability audit, measured baseline and safe local observability/session foundation. Settings can disable local reliability records; no remote analytics or crash uploads are implemented. See [the Phase 13 journal](docs/PHASE13_IMPLEMENTATION.md) and [event/privacy architecture](docs/OBSERVABILITY.md). Recovery/Safe Mode, performance aggregation, feedback and release gates remain subsequent milestones; this foundation does not establish Beta readiness.

Run the offline test suite, Python compilation check, and application smoke check from the project folder:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py src tests
python main.py --smoke-test
```

## Limitations

- Screen selection is limited to one display. Protected content, secure desktops, and some exclusive full-screen applications may not be capturable.
- AI output can be incomplete or incorrect. Review extracted values and suggested actions before relying on them.
- Provider transports do not stream or retry automatically. Authorized hybrid routing can make a cloud attempt after local failure or matching quality evidence. Cancellation/closing can discard late results but cannot retract content already sent, and a dispatched synchronous request may still finish.
- SnapFlow does not start automatically with Windows or install updates automatically.
- Phase 11 now includes client OAuth/elicitation, notifications/templates, a disabled-by-default SnapFlow server, controlled Workflow preview/execution and trusted SDK interfaces. Source/frozen evidence and remaining release limits are recorded in [the implementation report](docs/PHASE11_IMPLEMENTATION.md). See [client instructions](docs/extensions/MCP_CLIENT.md), [server setup](docs/extensions/MCP_SERVER.md), and [trusted development interfaces](docs/extensions/SNAPFLOW_EXTENSION_SDK.md).
- Visual Memory now has optional local text embeddings alongside keyword search and optional text-only AI questions. Memory-to-Workflow reuse and retention controls are not added by Phase 12.
- Phase 12 source functionality does not establish live local VLM, hardware-specific throughput/memory, frozen-build, installer/uninstaller or long-running performance qualification. See [the implementation record](docs/PHASE12_IMPLEMENTATION.md) for evidence and pending release checks.
