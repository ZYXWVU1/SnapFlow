"""Disposable frozen application checks; no inference or external writes."""
import json
from pathlib import Path
import zipfile


def run_packaged_reliability_smoke(app, controller):
    code = 0
    report = {'status': 'FAIL', 'qualified_inference': False, 'remote_requests': 0}
    try:
        from src.beta.feedback import FeedbackReport
        from src.beta.health import HealthCenterService
        from src.ui.beta_dialogs import NativeFeedbackDialog
        policy = controller.beta_features.policy
        if not policy.safe_mode or not controller.config.private_mode:
            raise RuntimeError('Qualification requires Private Mode and Safe Mode.')
        for operation in ('external_write', 'workflow_auto', 'mcp_auto', 'model_preload',
                          'cloud_fallback', 'extension_server', 'beta_feature'):
            if policy.allows(operation):
                raise RuntimeError('Safe Mode restriction failed.')
        snapshot = controller.safe_diagnostics_snapshot()
        dialog = NativeFeedbackDialog(controller.beta_feedback, diagnostics=snapshot)
        if dialog.attach_diagnostics.isChecked() or dialog.attach_screenshot.isChecked():
            raise RuntimeError('Feedback attachment consent defaults failed.')
        if dialog.diagnostics_preview.toPlainText() != snapshot.preview():
            raise RuntimeError('Diagnostics exact preview failed.')
        dialog.close()
        destination = controller.paths.data_dir.parent / 'phase13-feedback.zip'
        controller.beta_feedback.export(destination, FeedbackReport('Bug', 'Synthetic check',
                                            'Synthetic user-authored text'))
        with zipfile.ZipFile(destination) as archive:
            if archive.namelist() != ['feedback.json']:
                raise RuntimeError('Default feedback exported additional data.')
        invocation = controller.execution_journal.plan('integration_write')
        controller.execution_journal.started(invocation)
        controller.execution_journal.finish(invocation, 'unknown')
        health = HealthCenterService(controller.paths, config=controller.config,
                                    journal=controller.execution_journal).run()
        if health.status == 'healthy':
            raise RuntimeError('Unverified health was reported healthy.')
        controller.execution_journal.acknowledge(invocation)
        report.update(status='PASS', private_mode=True, safe_mode=True, consent_defaults=True,
                      exact_preview=True, feedback_content_only=True, manual_review=True,
                      unknown_health_visible=True)
    except Exception as error:
        report['error_type'] = type(error).__name__
        code = 1
    finally:
        controller.paths.data_dir.joinpath('phase13_smoke.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')
        controller.quit()
        if code:
            app.exit(code)
