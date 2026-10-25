"""Compact extraction coverage and explicit replacement review, desktop only."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QPushButton, QHeaderView


class ExtractionReviewSummary(QWidget):
    replace_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.replacements = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel('Run AI extraction to review all resume sections, or add facts manually.')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.coverage = QTreeWidget()
        self.coverage.setHeaderLabels(['Resume section', 'Result', 'Facts'])
        self.coverage.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.coverage.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.coverage.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.coverage.setMaximumHeight(110)
        self.coverage.setAccessibleName('Resume extraction coverage and unresolved sections')
        layout.addWidget(self.coverage)
        self.conflicts = QTreeWidget()
        self.conflicts.setHeaderLabels(['Field / record', 'Current value', 'Proposed replacement'])
        self.conflicts.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.conflicts.setMaximumHeight(120)
        self.conflicts.setAccessibleName('Resume replacement review')
        layout.addWidget(self.conflicts)
        self.use_replacement = QPushButton('Use selected replacement · review again before saving')
        self.use_replacement.clicked.connect(self._replace)
        layout.addWidget(self.use_replacement)
        self.coverage.hide()
        self.conflicts.hide()
        self.use_replacement.hide()

    def show_report(self, report, replacements):
        self.replacements = replacements
        counts = ', '.join(f'{count} {kind.replace("_", " ")}' for kind, count in report.get('counts', {}).items())
        unresolved = (len(report.get('rejected', [])) + len(report.get('conflicts', [])) +
            len(set(report.get('unprocessed_blocks', [])) | set(report.get('unrepresented_blocks', []))))
        state = 'Ready for your review' if report.get('complete_for_review') else 'Partial draft · check unresolved sections'
        self.summary.setText(f'{state}. {len(report.get("facts", []))} facts' +
            (f' · {counts}' if counts else '') + f' · {unresolved} unresolved items. Nothing is saved yet.')
        self.coverage.clear()
        names = {'found': 'Found · review required', 'not_found': 'No supported facts found', 'incomplete': 'Incomplete', 'needs_review': 'Needs review'}
        for section in report.get('sections', []):
            QTreeWidgetItem(self.coverage, [section['label'], names.get(section['status'], section['status']), str(section.get('facts', 0))])
        for warning in report.get('warnings', []):
            QTreeWidgetItem(self.coverage, ['Document / AI processing', warning.replace('_', ' '), ''])
        for rejected in report.get('rejected', []):
            QTreeWidgetItem(self.coverage, [rejected.get('key', 'Unclassified item'), 'Unsupported source · check manually', ''])
        self.coverage.setVisible(self.coverage.topLevelItemCount() > 0)
        self._show_replacements()

    def _show_replacements(self):
        self.conflicts.clear()
        for index, item in enumerate(self.replacements):
            if item.get('resolved'):
                continue
            def display(value):
                return '\n'.join(value) if isinstance(value, list) else str(value)
            row = QTreeWidgetItem(self.conflicts, [item.get('label', item['proposed']['key']),
                display(item['existing']['value']), display(item['proposed']['value'])])
            row.setData(0, Qt.ItemDataRole.UserRole, index)
        self.conflicts.setVisible(self.conflicts.topLevelItemCount() > 0)
        self.use_replacement.setVisible(self.conflicts.topLevelItemCount() > 0)

    def _replace(self):
        row = self.conflicts.currentItem()
        if row is not None:
            self.replace_requested.emit(row.data(0, Qt.ItemDataRole.UserRole))

    def clear(self):
        self.replacements = []
        self.coverage.clear()
        self.conflicts.clear()
        self.summary.clear()
