"""Readable preferences summary and user data backup controls."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel

from src.ui.design.components import AppButton, PageHeader
from src.ui.design.components import Card
from .common import ScrollPage
from src.app_version import APP_NAME, APP_VERSION


class SettingsPage(ScrollPage):
    edit_requested = Signal()
    backup_export_requested = Signal()
    backup_restore_requested = Signal()
    open_data_folder_requested = Signal()
    storage_usage_requested = Signal()
    support_bundle_requested = Signal()
    health_check_requested = Signal()
    usage_summary_requested = Signal()
    update_check_requested = Signal()
    onboarding_requested = Signal()
    memory_rebuild_requested = Signal()
    ai_runtime_requested = Signal()
    clear_analytics_requested = Signal()
    beta_features_requested = Signal()
    feedback_requested = Signal()
    health_center_requested = Signal()
    beta_insights_requested = Signal()
    report_issue_requested = Signal()

    def __init__(self, config=None, parent=None, *, paths=None):
        super().__init__(parent)
        self.paths = paths
        self.edit_button = AppButton('Edit Settings', variant='primary')
        self.edit_button.clicked.connect(self.edit_requested)
        self.content.addWidget(PageHeader('Settings',
            'Control capture, AI, and appearance preferences.', self.edit_button))
        capture = Card()
        capture.content.addWidget(QLabel('Capture'))
        capture.content.addWidget(QLabel('Hotkey'))
        self.hotkey_label = QLabel()
        capture.content.addWidget(self.hotkey_label)
        self.content.addWidget(capture)
        ai = Card()
        ai.content.addWidget(QLabel('AI'))
        ai.content.addWidget(QLabel('Default mode'))
        self.mode_label = QLabel()
        ai.content.addWidget(self.mode_label)
        self.ai_runtime_label = QLabel()
        self.ai_runtime_label.setWordWrap(True)
        self.ai_runtime_label.setTextFormat(Qt.TextFormat.PlainText)
        ai.content.addWidget(self.ai_runtime_label)
        self.execution_details_label = QLabel('No AI execution details recorded yet.')
        self.execution_details_label.setWordWrap(True)
        self.execution_details_label.setTextFormat(Qt.TextFormat.PlainText)
        self.execution_details_label.setProperty('role', 'muted')
        ai.content.addWidget(self.execution_details_label)
        self.ai_runtime_button = AppButton('AI & Models', variant='primary')
        self.ai_runtime_button.clicked.connect(self.ai_runtime_requested)
        ai.content.addWidget(self.ai_runtime_button)
        self.content.addWidget(ai)
        appearance = Card()
        appearance.content.addWidget(QLabel('Appearance'))
        self.theme_label = QLabel()
        appearance.content.addWidget(self.theme_label)
        self.content.addWidget(appearance)
        privacy = Card()
        privacy.content.addWidget(QLabel('Privacy'))
        self.privacy_note = QLabel()
        self.privacy_note.setWordWrap(True)
        self.privacy_note.setTextFormat(Qt.TextFormat.PlainText)
        self.privacy_note.setProperty('role', 'muted')
        privacy.content.addWidget(self.privacy_note)
        self.content.addWidget(privacy)
        memory = Card()
        memory.content.addWidget(QLabel('Visual Memory'))
        self.memory_status = QLabel()
        self.memory_status.setWordWrap(True)
        memory.content.addWidget(self.memory_status)
        self.rebuild_memory_index = AppButton('Rebuild Memory Search Index')
        self.rebuild_memory_index.clicked.connect(self.memory_rebuild_requested)
        memory.content.addWidget(self.rebuild_memory_index)
        self.content.addWidget(memory)
        context = Card()
        context.content.addWidget(QLabel('Contextual Intelligence'))
        self.context_status = QLabel()
        self.context_status.setWordWrap(True)
        context.content.addWidget(self.context_status)
        self.content.addWidget(context)

        diagnostics = Card()
        diagnostics.content.addWidget(QLabel('Diagnostics'))
        data_path = str(paths.data_dir) if paths else 'SnapFlow user data folder'
        log_path = str(paths.log_dir) if paths else 'SnapFlow logs folder'
        self.diagnostics_info_label = QLabel(
            f'{APP_NAME} {APP_VERSION}\nData directory: {data_path}\nLog directory: {log_path}')
        self.diagnostics_info_label.setWordWrap(True)
        self.diagnostics_info_label.setTextFormat(Qt.TextFormat.PlainText)
        diagnostics.content.addWidget(self.diagnostics_info_label)
        self.diagnostics_note = QLabel(
            'Preview the exact safe diagnostics before exporting a local ZIP. It excludes raw logs, '
            'screenshots, extracted data, configuration, user content, and credentials; it is never sent automatically.')
        self.diagnostics_note.setWordWrap(True)
        self.diagnostics_note.setProperty('role', 'muted')
        diagnostics.content.addWidget(self.diagnostics_note)
        self.reliability_status_label = QLabel()
        self._reliability_message = ''
        self._reliability_default = ''
        self.reliability_status_label.setWordWrap(True)
        self.reliability_status_label.setTextFormat(Qt.TextFormat.PlainText)
        diagnostics.content.addWidget(self.reliability_status_label)
        self.health_status_label = QLabel('Health check has not been run.')
        self.health_status_label.setWordWrap(True)
        self.health_status_label.setTextFormat(Qt.TextFormat.PlainText)
        diagnostics.content.addWidget(self.health_status_label)
        self.health_check_button = AppButton('Run Basic Health Check')
        self.health_check_button.clicked.connect(self.health_check_requested)
        diagnostics.content.addWidget(self.health_check_button)
        self.health_check_button.hide()
        self.health_status_label.hide()
        self.export_diagnostics_button = AppButton('Export Support Bundle')
        self.export_diagnostics_button.clicked.connect(self.support_bundle_requested)
        diagnostics.content.addWidget(self.export_diagnostics_button)
        self.clear_analytics_button = AppButton('Clear Usage & Diagnostics Data')
        self.clear_analytics_button.clicked.connect(self.clear_analytics_requested)
        diagnostics.content.addWidget(self.clear_analytics_button)
        self.health_center_button = AppButton('Health Center', variant='primary')
        self.health_center_button.clicked.connect(self.health_center_requested)
        diagnostics.content.addWidget(self.health_center_button)
        self.feedback_button = AppButton('Send Feedback')
        self.feedback_button.clicked.connect(self.feedback_requested)
        diagnostics.content.addWidget(self.feedback_button)
        self.report_issue_button = AppButton('Report This Issue')
        self.report_issue_button.setEnabled(False)
        self.report_issue_button.clicked.connect(self.report_issue_requested)
        diagnostics.content.addWidget(self.report_issue_button)
        self.beta_features_button = AppButton('Beta Features')
        self.beta_features_button.clicked.connect(self.beta_features_requested)
        diagnostics.content.addWidget(self.beta_features_button)
        self.beta_insights_button = AppButton('Beta Insights')
        self.beta_insights_button.setEnabled(False)
        self.beta_insights_button.clicked.connect(self.beta_insights_requested)
        diagnostics.content.addWidget(self.beta_insights_button)
        self.content.addWidget(diagnostics)

        usage = Card()
        usage.content.addWidget(QLabel('Usage & Costs'))
        self.runtime_usage_label = QLabel('No local or cloud AI requests recorded yet.')
        self.runtime_usage_label.setWordWrap(True)
        self.runtime_usage_label.setTextFormat(Qt.TextFormat.PlainText)
        usage.content.addWidget(self.runtime_usage_label)
        self.usage_summary_label = QLabel('No provider usage recorded this month.')
        self.usage_summary_label.setWordWrap(True)
        self.usage_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        usage.content.addWidget(self.usage_summary_label)
        self.usage_cost_label = QLabel('Cost estimate appears when the provider and model rate are known.')
        self.usage_cost_label.setWordWrap(True)
        self.usage_cost_label.setProperty('role', 'muted')
        usage.content.addWidget(self.usage_cost_label)
        self.usage_detail_label = QLabel()
        self.usage_detail_label.setWordWrap(True)
        self.usage_detail_label.setProperty('role', 'muted')
        self.usage_detail_label.setTextFormat(Qt.TextFormat.PlainText)
        usage.content.addWidget(self.usage_detail_label)
        self.usage_threshold_label = QLabel('Monthly warning: $5.00')
        usage.content.addWidget(self.usage_threshold_label)
        self.refresh_usage_button = AppButton('Refresh Usage')
        self.refresh_usage_button.clicked.connect(self.usage_summary_requested)
        usage.content.addWidget(self.refresh_usage_button)
        self.content.addWidget(usage)

        data = Card()
        data.content.addWidget(QLabel('Data & Backup'))
        data_note = QLabel('Backups include local settings, Visual Skills, Workflows, learning data, and saved examples. API keys and OAuth credentials are excluded.')
        data_note.setWordWrap(True)
        data_note.setProperty('role', 'muted')
        data.content.addWidget(data_note)
        self.data_location_label = QLabel(str(paths.data_dir) if paths else 'SnapFlow user data folder')
        self.data_location_label.setWordWrap(True)
        self.data_location_label.setTextInteractionFlags(self.data_location_label.textInteractionFlags())
        data.content.addWidget(self.data_location_label)
        self.storage_usage_label = QLabel('Not calculated')
        data.content.addWidget(self.storage_usage_label)
        buttons = QHBoxLayout()
        self.backup_export_button = AppButton('Export Backup', variant='primary')
        self.backup_restore_button = AppButton('Restore Backup')
        self.open_data_folder_button = AppButton('Open Data Folder')
        self.storage_usage_button = AppButton('Refresh Storage Usage')
        for button in (self.backup_export_button, self.backup_restore_button,
                       self.open_data_folder_button, self.storage_usage_button):
            buttons.addWidget(button)
        data.content.addLayout(buttons)
        self.backup_export_button.clicked.connect(self.backup_export_requested)
        self.backup_restore_button.clicked.connect(self.backup_restore_requested)
        self.open_data_folder_button.clicked.connect(self.open_data_folder_requested)
        self.storage_usage_button.clicked.connect(self.storage_usage_requested)
        self.content.addWidget(data)

        about = Card()
        about.content.addWidget(QLabel('About & Updates'))
        self.version_label = QLabel(f'{APP_NAME} {APP_VERSION}')
        about.content.addWidget(self.version_label)
        self.update_status_label = QLabel('Updates are checked only when you ask or enable startup checks.')
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setTextFormat(Qt.TextFormat.PlainText)
        about.content.addWidget(self.update_status_label)
        self.check_updates_button = AppButton('Check for Updates')
        self.check_updates_button.clicked.connect(self.update_check_requested)
        about.content.addWidget(self.check_updates_button)
        self.reopen_onboarding_button = AppButton('Run Setup Assistant')
        self.reopen_onboarding_button.clicked.connect(self.onboarding_requested)
        about.content.addWidget(self.reopen_onboarding_button)
        self.content.addWidget(about)
        self.content.addStretch(1)
        self.refresh(config)

    def refresh(self, config):
        if config is None:
            return
        self.hotkey_label.setText(' + '.join(part.capitalize() for part in config.hotkey.split('+')))
        self._reliability_default = (
            'Local reliability records: on. No automatic uploads.' if config.local_observability_enabled
            else 'Local reliability records: off. Abnormal-session detection is unavailable.')
        self.reliability_status_label.setText(self._reliability_message or self._reliability_default)
        self.mode_label.setText(config.default_mode.title())
        execution = getattr(config, 'ai_execution_mode', 'cloud_only').replace('_', ' ').title()
        private = getattr(config, 'private_mode', False)
        self.ai_runtime_label.setText(
            f'AI execution: {execution} · Private Mode {"on" if private else "off"}')
        self.privacy_note.setText(
            'Private Mode: external network access and cloud fallback are blocked. AI content stays on this computer.'
            if private else 'Screenshots are kept in memory. AI requests use the configured execution policy; cloud requests send selected content to your provider. Workflow History stores status only.')
        self.theme_label.setText(config.theme.title())
        self.memory_status.setText(
            ('Enabled' if config.visual_memory_enabled else 'Disabled for new saves') +
            ' · AI questions ' + ('enabled' if config.ai_memory_questions else 'off') +
            ' · Memory is stored locally until you delete it.')
        self.context_status.setText(
            ('Ask with Context enabled' if config.contextual_assistant_enabled else 'Ask with Context disabled') +
            ' · Whole-library search ' + ('allowed with session permission' if config.context_memory_search_enabled
                                            else 'off') +
            ' · New sessions use current screenshot only.')
        self.usage_threshold_label.setText(
            'Monthly warning: disabled' if config.monthly_cost_warning_usd == 0
            else f'Monthly warning: ${config.monthly_cost_warning_usd:.2f}')

    def set_reliability_status(self, message=''):
        self._reliability_message = message
        self.reliability_status_label.setText(message or self._reliability_default)

    def set_storage_usage(self, size_bytes):
        size = max(0, int(size_bytes))
        if size < 1024:
            label = f'{size} B'
        else:
            value = float(size)
            for unit in ('KB', 'MB', 'GB', 'TB'):
                value /= 1024
                if value < 1024 or unit == 'TB':
                    label = f'{value:.1f} {unit}'
                    break
        self.storage_usage_label.setText(label)

    def set_runtime_summary(self, summary):
        """Show measured runtime activity; unavailable latency stays unavailable."""
        def latency(key):
            value = summary.get(key)
            return 'Unavailable' if value is None else f'{value:.0f} ms'
        self.runtime_usage_label.setText(
            f'LOCAL: {summary.get("local_requests", 0)} request(s) ({summary.get("local_percentage", 0):.1f}%) · '
            f'CLOUD: {summary.get("cloud_requests", 0)} request(s) ({summary.get("cloud_percentage", 0):.1f}%)\n'
            f'Average local latency: {latency("avg_local_latency_ms")} · '
            f'Average cloud latency: {latency("avg_cloud_latency_ms")}\n'
            f'Cloud fallbacks: {summary.get("fallback_count", 0)} · '
            f'Private Mode requests: {summary.get("private_requests", 0)}')

    def set_execution_details(self, response, decision=None):
        """Expose the latest completed request without showing its prompt/content."""
        if response is None:
            self.execution_details_label.setText('No AI execution details recorded yet.')
            return
        reasons = tuple(getattr(decision, 'reason_codes', ()) or ())
        movement = 'LOCAL' if response.local else 'CLOUD'
        if any('FALLBACK' in reason.upper() for reason in reasons):
            movement = 'HYBRID → ' + movement
        if response.local:
            cost = 'API Cost: $0'
        else:
            estimate = getattr(decision, 'estimated_cost', None)
            cost = 'API cost estimate unavailable' if estimate is None else f'Estimated API cost: ${estimate:.4f}'
        reason_text = ', '.join(reason.replace('_', ' ') for reason in reasons) or 'Unavailable'
        self.execution_details_label.setText(
            f'Last AI request: {movement}\nRuntime: {response.runtime_id} · Model: {response.model_id}\n'
            f'Reason: {reason_text}\nLatency: {response.latency_ms:.0f} ms · {cost}')

    def set_usage_summary(self, summary, threshold):
        self.usage_summary_label.setText(
            f"This session: {summary.session_request_count} request(s) · Today: {summary.today_request_count} · "
            f"This month: {summary.request_count} API request(s) · "
            f"{summary.input_tokens:,} input tokens · {summary.output_tokens:,} output tokens "
            f"({summary.cached_input_tokens:,} cached input)")
        if summary.estimated_cost_usd is None:
            self.usage_cost_label.setText(
                f"Cost estimate unavailable for {summary.unknown_cost_calls} request(s) with unknown pricing.")
        else:
            self.usage_cost_label.setText(
                f"Estimated cost this month: ${summary.estimated_cost_usd:.4f}. "
                "This is not a provider invoice.")
        breakdown = '; '.join(f'{provider} / {model}: {count}'
                              for provider, model, count in summary.provider_model_breakdown[:4])
        details = [f'Recent failed requests today: {summary.recent_failure_count}']
        if breakdown:
            details.append('Requests by provider/model: ' + breakdown)
        self.usage_detail_label.setText(' · '.join(details))
        self.usage_threshold_label.setText(
            'Monthly warning: disabled' if threshold == 0
            else f'Monthly warning: ${threshold:.2f}')

    def set_update_status(self, message, *, checking=False):
        self.update_status_label.setText(message)
        self.check_updates_button.setEnabled(not checking)

    def set_health_check_running(self, running):
        self.health_check_button.setEnabled(not running)
        if running:
            self.health_status_label.setText('Checking local data, logs, and learning database…')

    def set_health_check_result(self, result):
        self.health_check_button.setEnabled(True)
        if result is None:
            self.health_status_label.setText('Health check failed. Export a support bundle for review.')
            return
        status = 'Healthy' if result.healthy else 'Needs attention'
        database = 'not created yet' if result.database_status == 'not created' else result.database_status
        self.health_status_label.setText(
            f'{status}\nData directory: {"writable" if result.data_writable else "not writable"}; '
            f'log directory: {"writable" if result.logs_writable else "not writable"}; '
            f'learning database: {database}.')
