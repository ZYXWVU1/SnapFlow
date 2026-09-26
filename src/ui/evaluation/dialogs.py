"""Dataset selection and inspection of measured evaluation cases."""
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QListWidget, QAbstractItemView, QMessageBox,
                              QPushButton, QScrollArea, QVBoxLayout, QWidget)

from src.ui.feedback.example_library import display_fields
from src.evaluation.config import current_model_identifier


class DatasetDialog(QDialog):
    def __init__(self, skill_storage, feedback_storage, dataset_storage, parent=None):
        super().__init__(parent)
        self.feedback_storage, self.dataset_storage = feedback_storage, dataset_storage
        self.setWindowTitle('Create Evaluation Dataset')
        self.resize(540, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.skill = QComboBox()
        for definition in skill_storage.list_skills():
            self.skill.addItem(definition.name, definition.id)
        self.kind = QComboBox()
        for value in ('development', 'validation', 'locked_test'):
            self.kind.addItem(value.replace('_', ' ').title(), value)
        form.addRow('Name', self.name)
        form.addRow('Skill', self.skill)
        form.addRow('Dataset type', self.kind)
        layout.addLayout(form)
        layout.addWidget(QLabel('Select verified examples'))
        self.examples = QListWidget()
        self.examples.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        layout.addWidget(self.examples)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        cancel, create = QPushButton('Cancel'), QPushButton('Create Dataset')
        cancel.clicked.connect(self.reject)
        create.clicked.connect(self.submit)
        row.addWidget(cancel)
        row.addWidget(create)
        layout.addLayout(row)
        self.skill.currentIndexChanged.connect(self.refresh_examples)
        self.refresh_examples()

    def refresh_examples(self):
        self.examples.clear()
        for record in self.feedback_storage.list_examples(self.skill.currentData()):
            if record.status != 'verified':
                continue
            self.examples.addItem(f'{record.created_at[:16].replace("T", " ")} · '
                                  f'{", ".join(record.changed_fields) or "Verified"}')
            self.examples.item(self.examples.count() - 1).setData(Qt.ItemDataRole.UserRole, record.id)

    def submit(self):
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.examples.selectedItems()]
        try:
            self.dataset = self.dataset_storage.create(self.name.text(), self.skill.currentData(),
                                                        self.kind.currentData(), ids)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class RunDialog(QDialog):
    def __init__(self, datasets, versions, parent=None):
        super().__init__(parent)
        self.datasets, self.versions = datasets, versions
        self.setWindowTitle('Run Evaluation')
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.dataset = QComboBox()
        for entry in datasets.list_datasets():
            self.dataset.addItem(f'{entry.name} · {entry.dataset_type}', entry.id)
        self.version = QComboBox()
        self.model = QLineEdit(current_model_identifier())
        form.addRow('Dataset', self.dataset)
        form.addRow('Skill version', self.version)
        form.addRow('Model identifier', self.model)
        layout.addLayout(form)
        self.dataset.currentIndexChanged.connect(self.refresh_versions)
        self.refresh_versions()
        self.error = QLabel()
        layout.addWidget(self.error)
        row = QHBoxLayout()
        cancel, start = QPushButton('Cancel'), QPushButton('Run Evaluation')
        cancel.clicked.connect(self.reject)
        start.clicked.connect(self.submit)
        row.addWidget(cancel)
        row.addWidget(start)
        layout.addLayout(row)

    def refresh_versions(self):
        self.version.clear()
        dataset = self.datasets.get(self.dataset.currentData())
        if dataset:
            for entry in self.versions.list_versions(dataset.skill_id):
                self.version.addItem(entry.version_label + ' · ' + entry.status, entry.version_id)

    def submit(self):
        if not self.dataset.currentData() or not self.version.currentData() or not self.model.text().strip():
            self.error.setText('Choose a dataset, Skill version and model identifier.')
            return
        self.accept()


class ReportDialog(QDialog):
    def __init__(self, report, report_storage, feedback_storage, parent=None):
        super().__init__(parent)
        self.report, self.feedback_storage = report, feedback_storage
        self.setWindowTitle('Evaluation Report')
        self.resize(740, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Skill: {report.skill_id} · Version: {report.skill_version_id}\n'
                                f'Dataset revision: {report.dataset_revision}\n'
                                f'Model: {report.model_config_id} · Cases: {report.test_case_count}'))
        for key, value in report.metrics.items():
            if key in ('denominators', 'case_count', 'usage_tokens'):
                continue
            display = 'N/A' if value is None else (f'{value:.1%}' if key != 'average_latency_ms' else f'{value:.0f} ms')
            layout.addWidget(QLabel(key.replace('_', ' ').title() + ': ' + display))
        layout.addWidget(QLabel('Measured cases: ' + str(report.metrics['case_count']) +
                                ' · Denominators: ' + str(report.metrics['denominators'])))
        if report.failures:
            layout.addWidget(QLabel('Unavailable cases: ' + '; '.join(report.failures)))
        self.cases = [case for case in report_storage.case_results(report.id)
                      if case['incorrect_fields'] or case['validation_errors']]
        layout.addWidget(QLabel('Failed cases'))
        self.case_list = QListWidget()
        for case in self.cases:
            self.case_list.addItem(case['example_id'][:8] + ' · ' + ', '.join(case['incorrect_fields'] or ['Validation error']))
        layout.addWidget(self.case_list)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.detail)
        self.image = QLabel()
        layout.addWidget(self.image)
        self.case_list.currentRowChanged.connect(self.show_case)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        layout.addWidget(close)

    def show_case(self, index):
        if not 0 <= index < len(self.cases):
            self.detail.clear()
            self.image.clear()
            return
        case = self.cases[index]
        self.detail.setText('Expected\n' + display_fields(case['expected']) + '\n\nActual\n' +
            display_fields(case['actual']) + '\n\nIncorrect: ' + ', '.join(case['incorrect_fields']) +
            '\nValidation: ' + '; '.join(case['validation_errors']))
        self.image.clear()
        example = self.feedback_storage.get(case['example_id'])
        if example and example.screenshot_reference and Path(example.screenshot_reference).is_file():
            self.image.setPixmap(QPixmap(example.screenshot_reference).scaled(420, 180,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
