"""Proposal review and background candidate comparison."""
import sqlite3
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from src.ui.skills.version_history import version_diff


class ImprovementSignals(QObject):
    finished = Signal(object, str)


class ProposalWorker(QRunnable):
    def __init__(self, optimizer, source_version_id, development_dataset_id):
        super().__init__()
        self.optimizer, self.source_version_id = optimizer, source_version_id
        self.development_dataset_id = development_dataset_id
        self.signals = ImprovementSignals()

    def run(self):
        try:
            candidate = self.optimizer.generate(self.source_version_id, self.development_dataset_id)
            self.signals.finished.emit(candidate, '')
        except Exception as exc:
            self.signals.finished.emit(None, f'Proposal failed: {type(exc).__name__}: {exc}')


class CandidateEvaluationWorker(QRunnable):
    def __init__(self, optimizer, runner, candidate, dataset_id, model_id):
        super().__init__()
        self.optimizer, self.runner, self.candidate = optimizer, runner, candidate
        self.dataset_id, self.model_id = dataset_id, model_id
        self.signals = ImprovementSignals()

    def run(self):
        try:
            baseline = self.runner.evaluate(self.candidate.source_version_id, self.dataset_id, self.model_id)
            proposed = self.runner.evaluate(self.candidate.candidate_version_id, self.dataset_id, self.model_id)
            candidate = self.optimizer.mark_evaluated(self.candidate.id, baseline.id, proposed.id)
            self.signals.finished.emit(candidate, '')
        except Exception as exc:
            self.signals.finished.emit(None, f'Comparison failed: {type(exc).__name__}: {exc}')


class ImprovementDialog(QDialog):
    evaluate_requested = Signal(str)
    changed = Signal()

    def __init__(self, optimizer, candidate, parent=None):
        super().__init__(parent)
        self.optimizer, self.candidate = optimizer, candidate
        self.setWindowTitle('Suggested Skill Improvement')
        self.resize(740, 650)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel('Proposed definition changes'))
        self.diff = QTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFontFamily('Consolas')
        layout.addWidget(self.diff, 2)
        layout.addWidget(QLabel('Current version vs candidate · measured on the same dataset'))
        self.metrics = QTextEdit()
        self.metrics.setReadOnly(True)
        layout.addWidget(self.metrics)
        row = QHBoxLayout()
        self.evaluate_button = QPushButton('Evaluate Candidate')
        self.publish_button = QPushButton('Publish Candidate')
        self.discard_button = QPushButton('Discard')
        close = QPushButton('Close')
        self.evaluate_button.clicked.connect(lambda: self.evaluate_requested.emit(self.candidate.id))
        self.publish_button.clicked.connect(self.publish)
        self.discard_button.clicked.connect(self.discard)
        close.clicked.connect(self.close)
        for button in (self.evaluate_button, self.publish_button, self.discard_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.candidate = self.optimizer.get(self.candidate.id)
        candidate = self.candidate
        self.status.setText(candidate.explanation + '\nStatus: ' + candidate.status.title())
        before = self.optimizer.version_manager.get(candidate.source_version_id)
        after = self.optimizer.version_manager.get(candidate.candidate_version_id)
        self.diff.setPlainText(version_diff(before, after) if before and after else 'Draft no longer available.')
        self.evaluate_button.setEnabled(candidate.status in ('proposed', 'evaluated'))
        self.publish_button.setEnabled(candidate.status == 'evaluated')
        self.discard_button.setEnabled(candidate.status in ('proposed', 'evaluated'))
        if candidate.status == 'evaluated':
            lines = []
            for key, (old, new) in self.optimizer.metric_comparison(candidate.id).items():
                if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
                    continue
                delta = new - old
                lower_better = key in ('hallucination_rate', 'average_latency_ms', 'usage_tokens')
                direction = ('unchanged' if not delta else
                    'improvement' if (delta < 0 if lower_better else delta > 0) else 'regression')
                unit = ' ms' if key == 'average_latency_ms' else ' tokens' if key == 'usage_tokens' else '%'
                previous = f'{old:.0f}' if unit != '%' else f'{old:.1%}'
                current = f'{new:.0f}' if unit != '%' else f'{new:.1%}'
                lines.append(f'{key.replace("_", " ").title()}: {previous}{unit if unit != "%" else ""} → '
                             f'{current}{unit if unit != "%" else ""} ({direction})')
            self.metrics.setPlainText('\n'.join(lines) or 'No comparable metrics available.')
        else:
            self.metrics.setPlainText('Evaluate on a validation or locked test dataset to compare actual results.')

    def publish(self):
        if QMessageBox.question(self, 'Publish Candidate',
            'Publish this evaluated candidate as the active Skill? Review any regressions above first.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.optimizer.approve(self.candidate.id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Cannot publish', str(exc))
            return
        self.refresh()
        self.changed.emit()

    def discard(self):
        if QMessageBox.question(self, 'Discard Candidate', 'Discard this unpublished draft?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.optimizer.reject(self.candidate.id)
        self.refresh()
        self.changed.emit()
