"""Optional, explicitly started llama-server process owned by SnapFlow.

No binaries are installed, downloaded, or run by construction. Use ``start``
in a Qt worker. A recent separately installed llama-server with ``--offline``
support is required. Diagnostics store fixed classifications, never raw output.
"""
from collections import deque
from src.observability.performance import measured
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from src.paths import AppPaths


class RuntimeProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeProcessState:
    runtime_id: str = "managed_llama"
    pid: int | None = None
    state: str = "stopped"
    model_id: str | None = None
    port: int | None = None
    started_at: str | None = None
    error: str | None = None


class ManagedLlamaRuntimeManager:
    """One owned subprocess, worker-safe state snapshots, no hidden restarts."""

    def __init__(self, paths=None, *, executable=None, idle_timeout_seconds=None,
                 process_factory=None, health_probe=None, log_limit=100):
        self.paths = paths or AppPaths()
        self.executable = Path(executable).expanduser() if executable else None
        if idle_timeout_seconds is not None and (not math.isfinite(idle_timeout_seconds)
                                                  or idle_timeout_seconds <= 0):
            raise ValueError("Idle timeout must be positive and finite.")
        self.idle_timeout_seconds = idle_timeout_seconds
        self._factory = process_factory or subprocess.Popen
        self._health_probe = health_probe or self._probe_health
        self._logs = deque(maxlen=max(1, min(int(log_limit), 1000)))
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._state = RuntimeProcessState()
        self._process = None
        self._starting = False
        self._reader = None
        self._api_key = None
        self._active_requests = 0
        self._last_activity = time.monotonic()
        self._last_model = None

    @property
    def state(self):
        with self._lock:
            self._check_crash()
            return self._state

    @property
    def endpoint(self):
        with self._lock:
            return f"http://127.0.0.1:{self._state.port}/v1" if self._state.port else None

    @property
    def api_key(self):
        with self._lock:
            return self._api_key

    @property
    def logs(self):
        with self._lock:
            return tuple(self._logs)

    def locate_executable(self):
        executable = self.executable or self.paths.runtimes_dir / ("llama-server.exe" if os.name == "nt" else "llama-server")
        executable = executable.resolve()
        if not executable.is_file():
            raise RuntimeProcessError("Runtime executable missing. Select a separately installed llama-server.")
        if os.name == "nt" and executable.suffix.lower() != ".exe":
            raise RuntimeProcessError("Managed runtime must be a llama-server executable.")
        if os.name != "nt" and not os.access(executable, os.X_OK):
            raise RuntimeProcessError("Runtime executable is not executable.")
        return executable

    @measured('model_load', 'local_runtime', event_type='model_load_completed')
    def start(self, model, *, timeout_seconds=60):
        """Start local data only; authenticated model listing verifies readiness.

        The ephemeral reservation closes just before launch because llama-server
        cannot inherit a listening socket. A concurrent port claim is detected
        by process exit and authenticated model identity checks, not accepted.
        """
        self._validate_timeout(timeout_seconds)
        launch_failure = None
        with self._lock:
            self._check_crash()
            if self._process is not None or self._starting:
                raise RuntimeProcessError("Stop the currently loaded runtime before starting another model.")
            executable = self.locate_executable()
            model_path = Path(model.model_path).resolve()
            if model.format != "gguf" or model.backend not in ("llama.cpp", "llama-server"):
                raise RuntimeProcessError("Unsupported model: managed llama-server requires GGUF for llama.cpp.")
            if not model_path.is_file():
                raise RuntimeProcessError("Model could not load: model file is missing.")
            projector = getattr(model, "projector_path", None)
            if model.capabilities.vision and not projector:
                raise RuntimeProcessError("Unsupported vision model: matching projector is required.")
            if projector and not Path(projector).is_file():
                raise RuntimeProcessError("Model could not load: projector file is missing.")
            self._logs.clear()
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._starting = True
            try:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
                key = secrets.token_urlsafe(32)
                argv = [str(executable), "--model", str(model_path), "--host", "127.0.0.1",
                        "--port", str(port), "--alias", model.id, "--api-key", key,
                        "--offline", "--n-gpu-layers", "0", "--no-webui", "--no-slots"]
                if projector:
                    argv += ["--mmproj", str(Path(projector).resolve()), "--no-mmproj-offload"]
                if model.capabilities.embeddings and not model.capabilities.text:
                    argv += ["--embeddings"]
                # Runtime environment flags can override CLI defaults, enable
                # remote download URLs, built-in shell tools, or external logs.
                blocked = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
                environment = {name: value for name, value in os.environ.items()
                               if not name.upper().startswith(("LLAMA_", "HF_", "MTMD_", "AIP_"))
                               and name.lower() not in blocked}
                options = {"shell": False, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                           "stderr": subprocess.STDOUT, "cwd": str(executable.parent),
                           "env": environment, "bufsize": 0}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW
                self._state = RuntimeProcessState(state="starting", model_id=model.id, port=port,
                                                  started_at=datetime.now(timezone.utc).isoformat())
                reservation.close()
                process = self._factory(argv, **options)
                self._process = process
                self._api_key = key
                self._last_model = model
                self._state = replace(self._state, pid=process.pid, state="loading_model")
                self._last_activity = time.monotonic()
                self._active_requests = 0
                reader = threading.Thread(target=self._read_diagnostics, args=(process,), daemon=True,
                                          name="SnapFlow-local-runtime-log")
                self._reader = reader
                reader.start()
            except Exception as exc:
                reservation.close()
                launch_failure = exc
        if launch_failure is not None:
            # stop takes its lifecycle lock before the state lock. Do not call
            # it while holding the state lock: an external stop may already be
            # waiting for that lock. The startup flag prevents another launch
            # from replacing our process while failed-start cleanup runs.
            try:
                self.stop(force=True)
            finally:
                with self._lock:
                    if self._process is None:
                        self._state = replace(self._state, state="error", pid=None, port=None,
                                              error="Runtime could not start. Verify the selected binary and model.")
                        self._logs.append(self._state.error)
                    self._starting = False
            if isinstance(launch_failure, RuntimeProcessError):
                raise launch_failure
            raise RuntimeProcessError(self._state.error) from launch_failure
        deadline = time.monotonic() + timeout_seconds
        try:
            while time.monotonic() < deadline:
                with self._lock:
                    if self._process is not process:
                        if self._state.error:
                            raise RuntimeProcessError(self._state.error)
                        raise RuntimeProcessError("Runtime startup was stopped.")
                    if process.poll() is not None:
                        raise RuntimeProcessError("Runtime crashed while loading the model.")
                remaining = deadline - time.monotonic()
                try:
                    healthy = self._health_probe(f"http://127.0.0.1:{port}", key, model.id,
                                                  max(0.001, min(1.0, remaining)))
                except Exception:
                    healthy = False
                if healthy:
                    with self._lock:
                        if self._process is not process:
                            raise RuntimeProcessError("Runtime startup was stopped.")
                        if process.poll() is not None:
                            raise RuntimeProcessError("Runtime crashed while loading the model.")
                        self._state = replace(self._state, state="ready")
                        monitor = threading.Thread(target=self._monitor, args=(process,), daemon=True,
                                                   name="SnapFlow-local-runtime-monitor")
                        monitor.start()
                        return self._state
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            raise RuntimeProcessError("Model loading timed out. Check model compatibility and available memory.")
        except RuntimeProcessError as exc:
            with self._lock:
                owned = self._process is process
            if owned:
                self.stop(force=True)
                with self._lock:
                    message = str(exc)
                    if "Insufficient memory to load the model." in self._logs:
                        message = ("The local model could not be loaded with the available system resources. "
                                   "Use a smaller model or another quantization.")
                    self._state = replace(self._state, state="error", model_id=model.id, error=message)
                    self._logs.append(message)
                    if message != str(exc):
                        raise RuntimeProcessError(message) from exc
            raise
        finally:
            with self._lock:
                self._starting = False

    def stop(self, *, force=False, timeout_seconds=5):
        self._validate_timeout(timeout_seconds)
        with self._stop_lock:
            with self._lock:
                process = self._process
                if process is None:
                    self._state = RuntimeProcessState()
                    return self._state
                if self._active_requests and not force:
                    raise RuntimeProcessError("Runtime has active requests; wait for them before unloading.")
                self._state = replace(self._state, state="stopping")
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout_seconds)
            except (OSError, subprocess.SubprocessError) as exc:
                with self._lock:
                    self._state = replace(self._state, state="error", error="Runtime could not stop.")
                raise RuntimeProcessError("Runtime could not stop.") from exc
            with self._lock:
                self._process = None
                self._api_key = None
                self._active_requests = 0
                self._state = RuntimeProcessState()
                stopped_state = self._state
                reader = self._reader
                self._reader = None
            # Waiting and joining never hold the state lock, keeping GUI
            # snapshots responsive and allowing diagnostic draining to finish.
            if reader is not None and reader.ident is not None and reader is not threading.current_thread():
                reader.join(timeout=min(timeout_seconds, 1.0))
            if process.stdout is not None and (reader is None or not reader.is_alive()):
                process.stdout.close()
            return stopped_state

    def restart(self, *, timeout_seconds=60):
        with self._lock:
            model = self._last_model
        if model is None:
            raise RuntimeProcessError("No model has been selected for restart.")
        self.stop()
        return self.start(model, timeout_seconds=timeout_seconds)

    def mark_request_started(self):
        with self._lock:
            self._check_crash()
            if self._state.state not in ("ready", "busy"):
                raise RuntimeProcessError("Local runtime is not ready.")
            if self._active_requests:
                raise RuntimeProcessError("Local runtime is busy; one inference request is allowed at a time.")
            self._active_requests += 1
            self._last_activity = time.monotonic()
            self._state = replace(self._state, state="busy")

    def mark_request_finished(self):
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_activity = time.monotonic()
            self._check_crash()
            if self._process is not None and self._active_requests == 0 and self._state.state == "busy":
                self._state = replace(self._state, state="ready")

    def unload_if_idle(self, *, now=None):
        with self._lock:
            if (self.idle_timeout_seconds is None or self._active_requests
                    or self._state.state != "ready" or self._process is None):
                return False
            if (time.monotonic() if now is None else now) - self._last_activity < self.idle_timeout_seconds:
                return False
        try:
            self.stop()
        except RuntimeProcessError:
            with self._lock:
                if self._active_requests:
                    return False
            raise
        return True

    def _check_crash(self):
        if self._process is not None and self._state.state not in ("stopping", "stopped", "error"):
            if self._process.poll() is not None:
                self._state = replace(self._state, pid=None, port=None, state="error",
                                      error="Runtime crashed. Restart the model when ready.")
                self._logs.append(self._state.error)
                self._api_key = None
                from src.reliability.crashes import record_background_error
                record_background_error(RuntimeProcessError('Local runtime process stopped unexpectedly.'),
                                        component='local_runtime')
                # Keep the owned process object until explicit stop drains logs.

    def _monitor(self, process):
        while True:
            time.sleep(0.25)
            with self._lock:
                if self._process is not process:
                    return
                self._check_crash()
                if self._state.state == "error":
                    return
            if self.unload_if_idle():
                return

    def _read_diagnostics(self, process):
        if process.stdout is None:
            return
        classifications = (("out of memory", "Insufficient memory to load the model."),
                           ("failed to load", "Model could not load."),
                           ("unsupported", "Runtime reported an unsupported model or option."),
                           ("unknown argument", "Runtime version does not support the required options."),
                           ("address already in use", "Loopback port unavailable."),
                           ("failed to bind", "Loopback port unavailable."))
        try:
            while True:
                # Limit each read: a process cannot allocate an unbounded line.
                chunk = process.stdout.readline(4096)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                lowered = decoded.lower()
                for needle, message in classifications:
                    if needle in lowered:
                        with self._lock:
                            self._logs.append(message)
                        break
        except (OSError, ValueError):
            pass

    @staticmethod
    def _validate_timeout(timeout):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ValueError("Runtime timeout must be positive and at most 600 seconds.")

    @staticmethod
    def _probe_health(endpoint, api_key, model_id, timeout_seconds):
        """Direct loopback connection: no proxies, DNS, redirects, or prompts.

        ``/health`` is public in llama.cpp; the protected ``/v1/models`` must
        report our explicit alias. A wrong API key must be rejected as well.
        """
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            return False
        deadline = time.monotonic() + timeout_seconds
        def read(path, key=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Health timeout")
            connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=remaining)
            try:
                headers = {"Authorization": "Bearer " + key} if key else {}
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                payload = response.read(65537)
                if len(payload) > 65536:
                    return response.status, None
                return response.status, json.loads(payload) if payload else None
            finally:
                connection.close()
        try:
            status, payload = read("/health")
            if status != 200 or not isinstance(payload, dict) or payload.get("status") != "ok":
                return False
            status, payload = read("/v1/models", api_key)
            if status != 200 or not isinstance(payload, dict):
                return False
            if not any(isinstance(item, dict) and item.get("id") == model_id for item in payload.get("data", ())):
                return False
            status, _ = read("/v1/models", "SnapFlow-invalid-" + secrets.token_hex(8))
            return status in (401, 403)
        except (OSError, ValueError, TypeError, http.client.HTTPException):
            return False
