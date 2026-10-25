"""Opt-in smoke-test setup and dispatch for source and packaged startup."""
from importlib import import_module
import os
import sys


# Descending order preserves priority when more than one phase is enabled.
_PHASES = {
    18: ('src.browser.packaged_smoke', 'run_packaged_browser_smoke'),
    17: ('src.automation.packaged_smoke', 'run_packaged_automation_smoke'),
    16: ('src.desktop_actions.packaged_smoke', 'run_packaged_action_smoke'),
    15: ('src.desktop_context.packaged_smoke', 'run_packaged_desktop_smoke'),
    14: ('src.agent.packaged_smoke', 'run_packaged_agent_smoke'),
    13: ('src.reliability.packaged_smoke', 'run_packaged_reliability_smoke'),
    11: ('src.mcp.packaged_smoke', 'run_packaged_mcp_smoke'),
    10: ('src.context.packaged_smoke', 'run_packaged_context_smoke'),
}


class SmokeTests:
    """Keep smoke-only behavior out of the desktop entry point."""

    def __init__(self):
        self._phases = tuple(
            phase for phase in _PHASES
            if f'--phase{phase}-smoke-test' in sys.argv
            and os.environ.get(f'SNAPFLOW_PHASE{phase}_SMOKE') == '1'
        )
        self._phase18_requested = '--phase18-smoke-test' in sys.argv
        self.enabled = '--smoke-test' in sys.argv or bool(self._phases)

    def prepare_startup(self) -> bool:
        """Install required isolation before desktop dependencies and data load."""
        if self._phase18_requested and 18 not in self._phases:
            if sys.stderr is not None:
                print('Phase 18 smoke requires explicit disposable-profile opt-in.', file=sys.stderr)
            return False
        for phase in self._phases:
            if phase < 14:
                continue
            module = import_module(_PHASES[phase][0])
            try:
                module.install_smoke_isolation()
            except (ValueError, OSError):
                profile = 'disposable profile' if phase >= 17 else 'disposable verification profile'
                print(f'Phase {phase} smoke requires its {profile}.', file=sys.stderr)
                return False
        return True

    def schedule(self, app, controller) -> None:
        """Schedule the selected check once the real application is ready."""
        if not self.enabled:
            return
        from PySide6.QtCore import QTimer

        if self._phases:
            phase = self._phases[0]
            module_name, function_name = _PHASES[phase]
            runner = getattr(import_module(module_name), function_name)
            arguments = (app,) if phase == 10 else (app, controller)
            QTimer.singleShot(0, lambda: runner(*arguments))
        else:
            QTimer.singleShot(500, controller.quit)
