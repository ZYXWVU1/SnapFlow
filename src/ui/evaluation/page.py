"""Learning dashboard backed only by local verified examples and runs."""
from pathlib import Path
import sqlite3

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMessageBox, QInputDialog,
                              QScrollArea, QVBoxLayout, QWidget)

from src.evaluation.datasets import DatasetStorage
from src.evaluation.config import current_model_identifier
from src.evaluation.runner import EvaluationRunner
from src.evaluation.storage import EvaluationStorage
from src.feedback.storage import FeedbackStorage, default_path
from src.skill_versions.manager import SkillVersionManager
from src.optimizer.service import SkillOptimizer
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING
from .dialogs import DatasetDialog, ReportDialog, RunDialog
from .worker import EvaluationWorker
from .improvements import ProposalWorker, CandidateEvaluationWorker, ImprovementDialog


class EvaluationPage(QScrollArea):
    open_skills_requested = Signal()

    def __init__(self, skills_storage, workflows_storage, client=None, learning_path=None):
        super().__init__()
        self.skills_storage, self.workflows_storage, self.client = skills_storage, workflows_storage, client
        self.learning_path = (Path(learning_path) if learning_path is not None else
            skills_storage.path.with_name('learning.sqlite3') if hasattr(skills_storage, 'path') else default_path())
        self.feedback = self.datasets = self.versions = self.reports = self.optimizer = None
        self.worker = None
        self.dialogs = []
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(SPACING['2xl'], SPACING['2xl'], SPACING['2xl'], SPACING['2xl'])
        layout.setSpacing(SPACING['lg'])
        layout.addWidget(PageHeader('Evaluation', 'Measure how Visual Skills improve from verified corrections.'))
        overview = Card()
        overview.content.addWidget(QLabel('Overview'))
        self.example_summary = QLabel('No Verified Examples Yet')
        self.run_summary = QLabel('No Evaluations Yet')
        self.version_summary = QLabel('0 published Skill versions')
        self.proposal_summary = QLabel('0 pending proposals')
        for label in (self.example_summary, self.run_summary, self.version_summary, self.proposal_summary):
            overview.content.addWidget(label)
        row = QHBoxLayout()
        self.skills_button = AppButton('Open Skills')
        self.skills_button.clicked.connect(self.open_skills_requested)
        row.addWidget(self.skills_button)
        self.create_button = AppButton('Create Dataset')
        self.create_button.clicked.connect(self.create_dataset)
        row.addWidget(self.create_button)
        self.run_button = AppButton('Run Evaluation', variant='primary')
        self.run_button.clicked.connect(self.run_evaluation)
        row.addWidget(self.run_button)
        overview.content.addLayout(row)
        layout.addWidget(overview)
        datasets_card = Card()
        datasets_card.content.addWidget(QLabel('Datasets'))
        self.dataset_list = QListWidget()
        self.dataset_list.setMaximumHeight(170)
        self.dataset_empty = QLabel('No datasets yet. Create one from verified examples.')
        datasets_card.content.addWidget(self.dataset_empty)
        datasets_card.content.addWidget(self.dataset_list)
        layout.addWidget(datasets_card)
        runs_card = Card()
        runs_card.content.addWidget(QLabel('Recent Evaluation Runs · accuracy trends (sample size shown)'))
        self.run_list = QListWidget()
        self.run_list.setMaximumHeight(170)
        self.run_empty = QLabel('No Evaluations Yet. Create a dataset and run your first Skill evaluation.')
        runs_card.content.addWidget(self.run_empty)
        self.run_list.itemDoubleClicked.connect(lambda _: self.show_report())
        runs_card.content.addWidget(self.run_list)
        layout.addWidget(runs_card)
        reliability = Card()
        reliability.content.addWidget(QLabel('Reliability'))
        self.failure_summary = QLabel('No measured failures yet.')
        self.failure_summary.setWordWrap(True)
        self.improved_summary = QLabel('No published improvements yet.')
        self.improved_summary.setWordWrap(True)
        reliability.content.addWidget(self.failure_summary)
        reliability.content.addWidget(self.improved_summary)
        layout.addWidget(reliability)
        improvements = Card()
        improvements.content.addWidget(QLabel('Skill Improvements'))
        self.proposal_list = QListWidget()
        self.proposal_list.setMaximumHeight(170)
        self.proposal_empty = QLabel('No proposals yet. Verified development examples can support an improvement proposal.')
        self.proposal_empty.setWordWrap(True)
        improvements.content.addWidget(self.proposal_empty)
        self.proposal_list.itemDoubleClicked.connect(lambda _: self.show_improvement())
        improvements.content.addWidget(self.proposal_list)
        self.generate_button = AppButton('Generate Improvement Proposal')
        self.generate_button.clicked.connect(self.generate_proposal)
        improvements.content.addWidget(self.generate_button)
        layout.addWidget(improvements)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)

    def _services(self):
        if not hasattr(self.skills_storage, 'path'):
            raise ValueError('Learning storage is unavailable.')
        if self.feedback is None:
            self.feedback = FeedbackStorage(self.learning_path)
            self.datasets = DatasetStorage(self.feedback, self.learning_path)
            self.versions = SkillVersionManager(self.skills_storage, self.workflows_storage, self.learning_path)
            self.reports = EvaluationStorage(self.feedback, self.learning_path)
            self.optimizer = SkillOptimizer(self.feedback, self.datasets, self.versions,
                                            self.client, self.learning_path, self.reports)

    def refresh(self):
        try:
            self._services()
            examples = self.feedback.list_examples()
            datasets = self.datasets.list_datasets()
            runs = self.reports.list_reports()
            proposals = self.optimizer.list_candidates()
            version_count = sum(sum(v.status == 'published' for v in self.versions.list_versions(s.id))
                                for s in self.skills_storage.list_skills())
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.status.setText('Unable to load evaluation data: ' + str(exc))
            return
        self.example_summary.setText(f'{len(examples)} verified example' + ('s' if len(examples) != 1 else '')
            if examples else 'No Verified Examples Yet · Correct a result and save it to begin.')
        self.run_summary.setText(f'{len(runs)} evaluation run' + ('s' if len(runs) != 1 else '')
            if runs else 'No Evaluations Yet · Create a dataset and run your first evaluation.')
        self.version_summary.setText(f'{version_count} published Skill version' + ('s' if version_count != 1 else ''))
        pending = sum(p.status in ('proposed', 'evaluated') for p in proposals)
        self.proposal_summary.setText(f'{pending} pending optimization proposal' + ('s' if pending != 1 else ''))
        self.dataset_list.clear()
        for entry in datasets:
            count = len(self.datasets.available_examples(entry))
            self.dataset_list.addItem(f'{entry.name} · {entry.skill_id} · {entry.dataset_type} · {count} available cases')
        self.dataset_empty.setVisible(not datasets)
        self.dataset_list.setVisible(bool(datasets))
        self.run_list.clear()
        self.run_ids = []
        for report in runs:
            accuracy = report.metrics.get('field_accuracy')
            accuracy_text = 'N/A' if accuracy is None else f'{accuracy:.0%}'
            self.run_list.addItem(f'{report.executed_at[:16].replace("T", " ")} · {report.skill_id} · '
                                  f'field accuracy {accuracy_text} · n={report.metrics.get("case_count", 0)}')
            self.run_ids.append(report.id)
        self.run_empty.setVisible(not runs)
        self.run_list.setVisible(bool(runs))
        self.proposal_list.clear()
        self.proposal_ids = []
        for proposal in proposals:
            self.proposal_list.addItem(f'{proposal.created_at[:16].replace("T", " ")} · '
                f'{proposal.skill_id} · {proposal.status.title()} · {proposal.explanation[:80]}')
            self.proposal_ids.append(proposal.id)
        self.proposal_empty.setVisible(not proposals)
        self.proposal_list.setVisible(bool(proposals))
        failure_counts = {}
        for report in runs[:10]:
            for case in self.reports.case_results(report.id):
                for field_id in case['incorrect_fields']:
                    failure_counts[field_id] = failure_counts.get(field_id, 0) + 1
        self.failure_summary.setText('Common failure fields (recent runs): ' +
            ', '.join(f'{field.replace("_", " ")}: {count}' for field, count in
                      sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[:5])
            if failure_counts else 'No measured failures yet.')
        improved = [p for p in proposals if p.status == 'approved'][:5]
        self.improved_summary.setText('Recently improved Skills: ' +
            ', '.join(f'{p.skill_id} ({p.created_at[:10]})' for p in improved)
            if improved else 'No published improvements yet.')
        self.status.clear()

    def create_dataset(self):
        try:
            self._services()
            dialog = DatasetDialog(self.skills_storage, self.feedback, self.datasets, self)
            self.dialogs.append(dialog)
            if dialog.exec():
                self.refresh()
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Dataset unavailable', str(exc))

    def run_evaluation(self):
        if self.worker is not None:
            return
        try:
            self._services()
            if self.client is None:
                raise ValueError('AI client is unavailable.')
            dialog = RunDialog(self.datasets, self.versions, self)
            self.dialogs.append(dialog)
            if not dialog.exec():
                return
            runner = EvaluationRunner(self.feedback, self.datasets, self.versions, self.reports, self.client)
            self.worker = EvaluationWorker(runner, dialog.version.currentData(),
                dialog.dataset.currentData(), dialog.model.text().strip())
            self.worker.signals.finished.connect(self.evaluation_finished)
            self.run_button.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.status.setText('Evaluating screenshots in the background…')
            QThreadPool.globalInstance().start(self.worker)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Evaluation unavailable', str(exc))

    def evaluation_finished(self, report, error):
        self.worker = None
        self.run_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self.refresh()
        self.show_report(report.id)

    def show_report(self, report_id=None):
        if report_id is None:
            index = self.run_list.currentRow()
            report_id = self.run_ids[index] if 0 <= index < len(self.run_ids) else None
        report = self.reports.get(report_id) if report_id else None
        if report:
            dialog = ReportDialog(report, self.reports, self.feedback, self)
            self.dialogs.append(dialog)
            dialog.show()

    def generate_proposal(self):
        if self.worker is not None:
            return
        try:
            self._services()
            datasets = [entry for entry in self.datasets.list_datasets()
                        if entry.dataset_type == 'development' and self.datasets.available_examples(entry)]
            if not datasets:
                raise ValueError('Create a development dataset of verified examples first.')
            labels = [f'{entry.name} · {entry.skill_id}' for entry in datasets]
            label, ok = QInputDialog.getItem(self, 'Development Dataset', 'Use verified development examples from:',
                                             labels, 0, False)
            if not ok:
                return
            dataset = datasets[labels.index(label)]
            source = next((v for v in reversed(self.versions.list_versions(dataset.skill_id))
                           if v.status == 'published'), None)
            if source is None:
                raise ValueError('Publish a custom Skill version first.')
            self.worker = ProposalWorker(self.optimizer, source.version_id, dataset.id)
            self.worker.signals.finished.connect(self.proposal_finished)
            self.generate_button.setEnabled(False)
            self.run_button.setEnabled(False)
            self.status.setText('Analyzing verified development feedback in the background…')
            QThreadPool.globalInstance().start(self.worker)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Proposal unavailable', str(exc))

    def proposal_finished(self, proposal, error):
        self.worker = None
        self.generate_button.setEnabled(True)
        self.run_button.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self.refresh()
        self.show_improvement(proposal.id)

    def show_improvement(self, proposal_id=None):
        if proposal_id is None:
            index = self.proposal_list.currentRow()
            proposal_id = self.proposal_ids[index] if 0 <= index < len(self.proposal_ids) else None
        proposal = self.optimizer.get(proposal_id) if proposal_id else None
        if proposal:
            dialog = ImprovementDialog(self.optimizer, proposal, self)
            dialog.evaluate_requested.connect(self.evaluate_candidate)
            dialog.changed.connect(self.refresh)
            self.dialogs.append(dialog)
            dialog.show()

    def evaluate_candidate(self, candidate_id):
        if self.worker is not None:
            return
        candidate = self.optimizer.get(candidate_id)
        if candidate is None:
            return
        datasets = [entry for entry in self.datasets.list_datasets(candidate.skill_id)
                    if entry.dataset_type in ('validation', 'locked_test') and self.datasets.available_examples(entry)]
        if not datasets:
            QMessageBox.warning(self, 'Dataset required', 'Create a validation or locked test dataset with saved screenshots.')
            return
        labels = [f'{entry.name} · {entry.dataset_type}' for entry in datasets]
        label, ok = QInputDialog.getItem(self, 'Compare Skill Versions', 'Evaluate both versions on:', labels, 0, False)
        if not ok:
            return
        model, ok = QInputDialog.getText(self, 'Model Configuration', 'Model identifier:',
                                         text=current_model_identifier())
        if not ok or not model.strip():
            return
        runner = EvaluationRunner(self.feedback, self.datasets, self.versions, self.reports, self.client)
        self.worker = CandidateEvaluationWorker(self.optimizer, runner, candidate,
            datasets[labels.index(label)].id, model.strip())
        self.worker.signals.finished.connect(self.candidate_finished)
        self.run_button.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.status.setText('Evaluating current and candidate versions in the background…')
        QThreadPool.globalInstance().start(self.worker)

    def candidate_finished(self, candidate, error):
        self.worker = None
        self.run_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self.refresh()
        for dialog in self.dialogs:
            if isinstance(dialog, ImprovementDialog) and dialog.candidate.id == candidate.id:
                dialog.refresh()
