"""Trusted desktop-only resume import and explicit editable fact review."""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from threading import Event
import time
import uuid
from urllib.parse import urlsplit
import os

from PySide6.QtCore import QEvent, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTabWidget,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QMessageBox, QCheckBox, QScrollArea)

from src.resume.importer import (ImportCancelled, MAX_TEXT_CHARS, ResumeImportError,
                                 extract_text, propose_facts, read_resume)
from src.resume.models import CATEGORIES, MAX_ENTITIES, MAX_FACTS
from src.resume.extraction import extraction_prompt, validate_extraction
from src.resume.categories import RECORD_KINDS, LIST_CATEGORIES, category_kind
from src.resume.document import document_from_text, read_resume_document
from src.resume.extraction_job import run_extraction
from .resume_extraction_review import ExtractionReviewSummary


# Workers own no dialog parent and may finish after Cancel. This keeps Qt from
# destroying a running thread while ensuring cancelled results are discarded.
_IMPORT_WORKERS = set()


def cancel_resume_imports():
    """Application shutdown hook: discard all pending file/model results."""
    for worker in tuple(_IMPORT_WORKERS):
        worker.cancelled.set()


def resume_imports_running():
    """Let the normal app quit poll wait for cancelled workers to unwind."""
    return any(worker.isRunning() for worker in tuple(_IMPORT_WORKERS))


class _FactTextEditor(QStyledItemDelegate):
    """Permit real multiline skills/descriptions/source excerpts in review rows."""
    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.ItemDataRole.EditRole) or '')
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rectangle = option.rect
        rectangle.setHeight(max(rectangle.height(), 90))
        editor.setGeometry(rectangle)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
            return True
        return super().eventFilter(editor, event)


def _policy_stamp(client):
    config = getattr(client, 'config', None)
    settings = asdict(config) if is_dataclass(config) else vars(config) if hasattr(config, '__dict__') else config
    return sha256(json.dumps({'config': settings,
        'private': bool(getattr(getattr(client, 'policy', None), 'private_mode', False))},
        sort_keys=True, default=str).encode()).hexdigest()


class _ExtractionWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, client, source, choice):
        super().__init__()
        self.client, self.source, self.choice = client, source, choice
        self.cancelled = Event()
        self.policy_stamp = _policy_stamp(client)
        self.deadline = time.monotonic() + 300

    def is_cancelled(self):
        return (self.cancelled.is_set() or
                _policy_stamp(self.client) != self.policy_stamp)

    def run(self):
        try:
            if self.is_cancelled():
                return
            source = self.source if isinstance(self.source, dict) else document_from_text(self.source)
            result = run_extraction(self.client, source, self.choice,
                cancelled=self.is_cancelled, progress=self.progress.emit)
            if not self.is_cancelled():
                self.succeeded.emit(result)
        except Exception:
            if not self.is_cancelled():
                self.failed.emit('AI extraction failed or returned unsupported facts. Continue with manual review, '
                    'or check the selected runtime in Settings → AI & Models.')
        finally:
            self.source = None


class _ImportWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.cancelled = Event()

    def run(self):
        try:
            text = read_resume_document(self.path, cancel=self.cancelled, isolated=True)
            if not self.cancelled.is_set():
                self.succeeded.emit(text)
            text = ''
        except ImportCancelled:
            pass
        except ResumeImportError as exc:
            if not self.cancelled.is_set():
                self.failed.emit(str(exc))
        except Exception:
            if not self.cancelled.is_set():
                self.failed.emit('Unable to import this resume; paste text instead.')
        finally:
            self.path = None


class _PagePreviewWorker(QThread):
    succeeded = Signal(bytes)
    failed = Signal()

    def __init__(self, data, page):
        super().__init__()
        self.data, self.page = data, page
        self.cancelled = Event()

    def run(self):
        try:
            from src.resume.pdf_pages import render_pdf_page
            image = render_pdf_page(self.data, self.page, cancel=self.cancelled)
            if not self.cancelled.is_set():
                self.succeeded.emit(image)
        except Exception:
            if not self.cancelled.is_set():
                self.failed.emit()
        finally:
            self.data = None


class ResumeProfileDialog(QDialog):
    """Create/edit a local profile. Only Save writes to the supplied ResumeStore.

    Public helpers also support deterministic desktop tests. A file path is
    selected exclusively by this dialog; no bridge payload is interpreted here.
    """
    profile_saved = Signal(dict)
    settings_requested = Signal()

    def __init__(self, store, parent=None, profile_id=None, *, client=None):
        super().__init__(parent)
        self.store = store
        self.client = client
        self.saved_profile = None
        self._profile = {'id': '', 'revision': 0, 'name': 'My resume', 'facts': [], 'entities': [], 'schema_version': 2}
        self._worker = None
        self._ai_worker = None
        self._ai_snapshot = None
        self._loading = False
        self._closed = False
        self._document = None
        self._pending_ai = False
        self._extraction_report = None
        self._replacements = []
        self._preview_worker = None
        self._preview_page = None
        self._preview_source_fingerprint = None
        self._page_previews = set()
        self._setting_source = False
        if profile_id:
            profile = store.get_profile(profile_id)
            if profile is None:
                raise ValueError('Resume profile does not exist.')
            self._profile = profile
        self.setWindowTitle('Review resume profile')
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        disclosure = QLabel('Resume facts and source excerpts are saved locally only when you choose Save. '
            'Only facts you check as reviewed can be used to fill forms. AI extraction runs only when you explicitly request it below. '
            'Deleting a profile removes current local records; exported backups must be deleted separately.')
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        form = QFormLayout()
        self.name_edit = QLineEdit(self._profile['name'])
        self.name_edit.setMaxLength(200)
        form.addRow('Profile name', self.name_edit)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_import_tab()
        self._build_facts_tab()
        self._build_entities_tab()
        self.status_label = QLabel('Paste text or choose a file, then review and categorize facts. You may also add facts manually.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_profile)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        self._loading = True
        for entity in self._profile['entities']:
            self.add_entity(entity['kind'], entity['label'], entity['start_date'], entity['end_date'], entity_id=entity['id'])
        for fact in self._profile['facts']:
            self.add_fact(fact['key'], fact['value'], fact['entity_id'], fact['source'], fact['reviewed'], provenance=fact.get('provenance'))
        self._loading = False
        self.refresh_groups()

    def _build_import_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        introduction = QLabel('Choose a PDF or DOCX (5 MiB maximum), or paste resume text. AI extracts contact details, all jobs and education, projects, skills, credentials and other sections for your review.')
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        self.source_edit = QPlainTextEdit()
        self.source_edit.textChanged.connect(self._source_changed)
        self.source_edit.setPlaceholderText('Paste resume text here. Review facts in the next tab; nothing is saved yet.')
        layout.addWidget(self.source_edit)
        actions = QHBoxLayout()
        self.import_button = QPushButton('Choose file and extract with AI…')
        self.import_button.clicked.connect(self.choose_file)
        actions.addWidget(self.import_button)
        self.extract_button = QPushButton('Basic import · contacts only')
        self.extract_button.clicked.connect(self.import_pasted_text)
        actions.addWidget(self.extract_button)
        self.cancel_import_button = QPushButton('Cancel import')
        self.cancel_import_button.setEnabled(False)
        self.cancel_import_button.clicked.connect(self.cancel_import)
        actions.addWidget(self.cancel_import_button)
        layout.addLayout(actions)
        ai_actions = QHBoxLayout()
        self.runtime_choice = QComboBox()
        self.runtime_choice.addItem('Configured provider', 'configured')
        self.runtime_choice.addItem('Local only', 'local_only')
        self.runtime_choice.addItem('Cloud — sends full resume text', 'cloud_only')
        ai_actions.addWidget(self.runtime_choice)
        self.ai_extract_button = QPushButton('Extract resume with AI')
        self.ai_extract_button.setEnabled(self.client is not None)
        self.ai_extract_button.clicked.connect(self.extract_with_ai)
        ai_actions.addWidget(self.ai_extract_button)
        layout.addLayout(ai_actions)
        settings_button = QPushButton('Open AI && Models settings')
        settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(settings_button)
        self.inspect_pages = QCheckBox('Also inspect all PDF pages visually with the selected AI model')
        self.inspect_pages.setEnabled(False)
        layout.addWidget(self.inspect_pages)
        note = QLabel('Before AI processing, confirm the provider/model and whether text and page images will be sent. '
            'Basic import finds limited contacts. AI results remain unreviewed until you check and save them.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs.addTab(page, '1. Import / source')

    def _build_facts_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.extraction_summary = ExtractionReviewSummary()
        self.extraction_summary.replace_requested.connect(self.use_replacement)
        layout.addWidget(self.extraction_summary)
        note = QLabel('Choose a category and record, edit its exact value and source excerpt, then check Reviewed. '
            'For skills, enter one item per line. Dates accept YYYY, YYYY-MM or YYYY-MM-DD; use Present only for an explicit current end date. '
            'Create or choose the matching record for repeated details. Editing a fact clears its review check.')
        note.setWordWrap(True)
        layout.addWidget(note)
        groups = QHBoxLayout()
        self.group_filter = QComboBox()
        self.group_filter.setAccessibleName('Resume fact group')
        self.group_filter.currentIndexChanged.connect(self.filter_group)
        groups.addWidget(self.group_filter, 1)
        self.review_group_button = QPushButton('Mark displayed group reviewed')
        self.review_group_button.clicked.connect(self.mark_group_reviewed)
        groups.addWidget(self.review_group_button)
        layout.addLayout(groups)
        self.facts_table = QTableWidget(0, 5)
        self.facts_table.setHorizontalHeaderLabels(['Reviewed', 'Category', 'Record', 'Value', 'Source excerpt'])
        self.facts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.facts_table.horizontalHeader().setStretchLastSection(True)
        self.facts_table.setColumnWidth(0, 75)
        self.facts_table.setColumnWidth(1, 210)
        self.facts_table.setColumnWidth(2, 150)
        self.facts_table.setColumnWidth(3, 235)
        self.facts_table.setItemDelegateForColumn(3, _FactTextEditor(self.facts_table))
        self.facts_table.setItemDelegateForColumn(4, _FactTextEditor(self.facts_table))
        self.facts_table.itemChanged.connect(self._fact_changed)
        self.facts_table.currentCellChanged.connect(self.show_fact_evidence)
        layout.addWidget(self.facts_table)
        self.evidence_preview = QPlainTextEdit()
        self.evidence_preview.setReadOnly(True)
        self.evidence_preview.setMaximumHeight(100)
        self.evidence_preview.setAccessibleName('Source evidence for selected resume fact')
        self.evidence_preview.setPlaceholderText('Select a fact to inspect its source page, quoted text and any normalization.')
        layout.addWidget(self.evidence_preview)
        preview_actions = QHBoxLayout()
        self.page_preview_button = QPushButton('Preview original PDF page')
        self.page_preview_button.setEnabled(False)
        self.page_preview_button.clicked.connect(self.preview_selected_page)
        preview_actions.addWidget(self.page_preview_button)
        self.hide_preview_button = QPushButton('Hide page preview')
        self.hide_preview_button.clicked.connect(lambda: self.page_image.hide())
        preview_actions.addWidget(self.hide_preview_button)
        layout.addLayout(preview_actions)
        self.page_image = QLabel()
        self.page_image.setAccessibleName('Original PDF page for evidence review')
        self.page_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_image.setMaximumHeight(230)
        self.page_image.hide()
        layout.addWidget(self.page_image)
        buttons = QHBoxLayout()
        add = QPushButton('Add fact')
        add.clicked.connect(lambda: self.add_fact())
        buttons.addWidget(add)
        remove = QPushButton('Remove selected facts')
        remove.clicked.connect(self.remove_selected_facts)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.tabs.addTab(page, '2. Review facts')

    def _build_entities_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel('Give each job or education record a label and dates so form groups can select the right record. '
            'Start/end dates preserve known precision. Blank end dates mean no end date was supplied; '
            'they do not establish current employment. Use Present as an end date only when current status is explicit. '
            'Add the corresponding date facts if they should fill form fields.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.entities_table = QTableWidget(0, 4)
        self.entities_table.setHorizontalHeaderLabels(['Kind', 'Record label', 'Start date', 'End date'])
        self.entities_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entities_table.itemChanged.connect(self._entity_changed)
        layout.addWidget(self.entities_table)
        buttons = QHBoxLayout()
        self.record_kind = QComboBox()
        for kind in RECORD_KINDS:
            self.record_kind.addItem(kind.title(), kind)
        buttons.addWidget(self.record_kind)
        add_record = QPushButton('Add record')
        add_record.clicked.connect(lambda: self.add_entity(self.record_kind.currentData()))
        buttons.addWidget(add_record)
        remove = QPushButton('Remove selected records and their facts')
        remove.clicked.connect(self.remove_selected_entities)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.tabs.addTab(page, '3. Resume records')

    def _set_busy(self, busy):
        self.import_button.setEnabled(not busy)
        self.extract_button.setEnabled(not busy)
        self.cancel_import_button.setEnabled(busy)
        self.save_button.setEnabled(not busy)
        self.ai_extract_button.setEnabled(not busy and self.client is not None)
        self.runtime_choice.setEnabled(not busy)
        self.review_group_button.setEnabled(not busy)
        self.inspect_pages.setEnabled(not busy and self._document is not None and self._document['kind'] == 'pdf')
        self.source_edit.setReadOnly(self._worker is not None)

    def choose_file(self):
        selected, _ = QFileDialog.getOpenFileName(self, 'Choose resume', '', 'Resume documents (*.pdf *.docx)')
        if selected:
            self._start_file_import(selected)

    def _start_file_import(self, selected):
        self.cancel_import()
        self._clear_page_preview()
        self._document = None
        self._pending_ai = True
        worker = _ImportWorker(selected)
        self._worker = worker
        _IMPORT_WORKERS.add(worker)
        worker.succeeded.connect(self._file_imported)
        worker.failed.connect(self._import_failed)
        worker.finished.connect(self._import_finished)
        worker.finished.connect(lambda: _IMPORT_WORKERS.discard(worker))
        self._set_busy(True)
        self.status_label.setText('Reading selected resume locally…')
        worker.start()

    def _file_imported(self, document):
        if not self._closed and self.sender() is self._worker and not self._worker.cancelled.is_set():
            self._setting_source = True
            try:
                self.source_edit.setPlainText('\n'.join(b['text'] for b in document['blocks']))
            finally:
                self._setting_source = False
            self._document = document
            self.inspect_pages.setEnabled(document['kind'] == 'pdf')
            self.status_label.setText('Document read locally. Confirm the provider to extract all resume sections.')

    def _import_failed(self, message):
        if not self._closed and self.sender() is self._worker:
            self._pending_ai = False
            self._document = None
            self.status_label.setText(message)

    def _import_finished(self):
        if self.sender() is self._worker:
            self._worker = None
            if not self._closed:
                self._set_busy(False)
                if self._pending_ai and self._document is not None:
                    self._pending_ai = False
                    QTimer.singleShot(0, self.extract_with_ai)

    def cancel_import(self):
        self._pending_ai = False
        if self._preview_worker is not None:
            self._preview_worker.cancelled.set()
            self._preview_worker = None
        if self._worker:
            self._worker.cancelled.set()
            self._worker = None
        if self._ai_worker:
            self._ai_worker.cancelled.set()
            self._ai_worker = None
        self._ai_snapshot = None
        if hasattr(self, 'save_button'):
            self._set_busy(False)

    def _source_changed(self):
        if self._setting_source:
            return
        self._clear_page_preview()
        self._document = None
        if hasattr(self, 'inspect_pages'):
            self.inspect_pages.setChecked(False)
            self.inspect_pages.setEnabled(False)
        if self._worker:
            self.cancel_import()
            self.status_label.setText('Source changed; the pending file import was cancelled.')
        if self._ai_worker:
            self.cancel_import()
            self.status_label.setText('Source changed; AI extraction was cancelled. Request extraction again when ready.')

    def _review_snapshot(self):
        return (self.source_edit.toPlainText(), json.dumps({'entities': self._entities(),
            'facts': self._facts()}, sort_keys=True, ensure_ascii=False))

    def _selected_route(self):
        choice = self.runtime_choice.currentData()
        config = getattr(self.client, 'config', None)
        private = bool(getattr(config, 'private_mode', False) or getattr(getattr(self.client, 'policy', None), 'private_mode', False))
        mode = getattr(config, 'ai_execution_mode', 'cloud_only')
        if private or mode == 'local_only':
            if choice == 'cloud_only':
                raise ValueError('Current privacy settings require local extraction. Choose Local only.')
            return 'local_only'
        if choice in ('local_only', 'cloud_only'):
            return choice
        if mode == 'ask_every_time':
            raise ValueError('Choose Local only or Cloud for this extraction.')
        if mode in ('prefer_local', 'automatic'):
            return 'local_only' if getattr(config, 'local_ai_model', None) or not getattr(config, 'allow_cloud_fallback', False) else 'cloud_only'
        return 'cloud_only'

    def _confirm_extraction(self, document, choice):
        config = getattr(self.client, 'config', None)
        local = choice == 'local_only'
        model = (getattr(config, 'local_ai_model', None) if local else getattr(config, 'ai_model', None) or os.getenv('AI_MODEL'))
        if not model:
            from src.config import DEFAULT_AI_MODEL
            model = 'configured local model' if local else DEFAULT_AI_MODEL
        endpoint = (getattr(config, 'local_ai_endpoint', None) if local else getattr(config, 'ai_base_url', None) or os.getenv('AI_BASE_URL'))
        provider = 'this computer' if local else urlsplit(endpoint or 'https://api.openai.com/v1').hostname or 'configured cloud provider'
        pages = len(document.get('pages', []))
        content = ('Resume text and, if needed, PDF page images will be processed locally.' if local else
            'Resume text and, if needed, PDF page images will be sent to this provider.')
        message = (f'Provider: {provider}\nModel: {str(model)[:200]}\n'
            f'Scope: {pages} page(s), {sum(len(b["text"]) for b in document["blocks"]):,} text characters.\n\n'
            + content + '\nExtraction includes section and coverage checks, up to 32 bounded requests. '
            'Results stay unreviewed until you check and save them.\n\nStart this extraction?')
        return QMessageBox.question(self, 'Confirm resume AI processing', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def extract_with_ai(self):
        if self._closed or self.client is None or self._worker or self._ai_worker:
            return
        try:
            source = deepcopy(self._document) if self._document else document_from_text(self.source_edit.toPlainText())
            if source['kind'] == 'pdf' and self.inspect_pages.isChecked():
                for page in source['pages']:
                    page['needs_vision'] = True
            choice = self._selected_route()
            stamp = _policy_stamp(self.client)
            if not self._confirm_extraction(source, choice):
                self.status_label.setText('AI extraction was not started. Your resume has not been sent.')
                return
            if stamp != _policy_stamp(self.client):
                raise ValueError('Provider or privacy settings changed. Confirm the current route again.')
            worker = _ExtractionWorker(self.client, deepcopy(source), choice)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._ai_snapshot = self._review_snapshot()
        self._ai_worker = worker
        _IMPORT_WORKERS.add(worker)
        worker.succeeded.connect(self._ai_extracted)
        worker.failed.connect(self._ai_failed)
        worker.progress.connect(lambda message: self.status_label.setText(message)
            if self._ai_worker is worker and not self._closed else None)
        worker.finished.connect(self._ai_finished)
        worker.finished.connect(lambda: _IMPORT_WORKERS.discard(worker))
        self._set_busy(True)
        self.status_label.setText('Extracting resume facts with the explicitly selected runtime. '
            'All results require review; Cancel import discards the result.')
        worker.start()

    def _ai_extracted(self, result):
        worker = self.sender()
        if self._closed or worker is not self._ai_worker or worker.is_cancelled():
            return
        if self._review_snapshot() != self._ai_snapshot:
            self.status_label.setText('Facts changed during extraction; the stale result was discarded. Request extraction again if needed.')
            return
        entities = self._entities()
        facts = self._facts()
        existing = {(fact['key'], fact['entity_id']): fact for fact in facts}
        entity_map = {}
        # Reuse a stable record only for an exact reviewed description; otherwise
        # create new opaque IDs rather than retaining provider-controlled IDs.
        def fingerprint(entity):
            return tuple(entity[key] for key in ('kind', 'label', 'start_date', 'end_date'))
        for entity in result['entities']:
            candidate_facts = {f['key']: f['value'] for f in result['facts'] if f['entity_id'] == entity['id']}
            def compatible(old):
                old_facts = {f['key']: f['value'] for f in facts if f['entity_id'] == old['id']}
                if fingerprint(old) != fingerprint(entity):
                    return False
                same_identity = [e for e in result['entities'] if fingerprint(e) == fingerprint(entity)]
                if len(same_identity) == 1:
                    return True
                # Different promotions/degrees may share employer and year-only dates.
                identity_keys = [k for k in candidate_facts if k.endswith(('.title', '.role', '.degree', '.field_of_study'))]
                return bool(identity_keys) and all(old_facts.get(k) == candidate_facts[k] for k in identity_keys)
            matches = [old for old in entities if compatible(old)]
            match = matches[0] if len(matches) == 1 else None
            if match:
                entity_map[entity['id']] = match['id']
            else:
                eid = self.add_entity(entity['kind'], entity['label'], entity['start_date'], entity['end_date'])
                entity_map[entity['id']] = eid
        added = 0
        self._replacements = []
        for fact in result['facts']:
            eid = entity_map.get(fact['entity_id']) if fact['entity_id'] is not None else None
            if fact['entity_id'] is not None and eid is None:
                continue
            if (fact['key'], eid) not in existing:
                self.add_fact(fact['key'], fact['value'], eid, fact['source'], False, provenance=fact.get('provenance'))
                existing[(fact['key'], eid)] = {**fact, 'entity_id': eid}
                added += 1
            elif existing[(fact['key'], eid)]['value'] != fact['value']:
                self._replacements.append(dict(existing=existing[(fact['key'], eid)],
                    proposed={**fact, 'entity_id': eid}, label=CATEGORIES[fact['key']]))
        for conflict in result.get('conflicts', []):
            proposed = deepcopy(conflict['proposed'])
            proposed['entity_id'] = entity_map.get(proposed['entity_id'], proposed['entity_id'])
            key = proposed['key'], proposed['entity_id']
            if key in existing:
                self._replacements.append(dict(existing=existing[key], proposed=proposed, label=CATEGORIES[proposed['key']]))
        self._extraction_report = result
        self.extraction_summary.show_report(result, self._replacements)
        self.refresh_groups()
        state = 'Draft ready for review' if result.get('complete_for_review') else 'Partial extraction · review missing or unsupported details'
        self.status_label.setText(f'{state}. {added} new unreviewed facts; {len(self._replacements)} replacements need a choice. Check each group, then Save.')
        self.tabs.setCurrentIndex(1)

    def _ai_failed(self, message):
        if not self._closed and self.sender() is self._ai_worker:
            self.status_label.setText(message)

    def _ai_finished(self):
        if self.sender() is self._ai_worker:
            cancelled = self._ai_worker.is_cancelled()
            self._ai_worker = None
            self._ai_snapshot = None
            if not self._closed:
                self._set_busy(False)
                if cancelled:
                    self.status_label.setText('AI extraction was cancelled or its runtime policy changed. Continue with manual review or request extraction again.')

    def import_pasted_text(self):
        try:
            self.set_source_text(self.source_edit.toPlainText())
        except ResumeImportError as exc:
            self.status_label.setText(str(exc))

    def set_source_text(self, text):
        text = extract_text(text)
        self.source_edit.setPlainText(text)
        existing = {(self.facts_table.cellWidget(row, 1).currentData(),
                     self.facts_table.cellWidget(row, 2).currentData())
                    for row in range(self.facts_table.rowCount())}
        added = 0
        for fact in propose_facts(text):
            if (fact['key'], fact['entity_id']) not in existing:
                self.add_fact(fact['key'], fact['value'], fact['entity_id'], fact['source'], False)
                added += 1
        self.status_label.setText(f'Imported text is ready. {added} unreviewed candidate(s) added. '
            'Basic import finds limited contacts. Use Extract resume with AI for the full resume.')
        self.tabs.setCurrentIndex(1)

    def add_entity(self, kind='experience', label='', start_date='', end_date='', *, entity_id=None):
        if self.entities_table.rowCount() >= MAX_ENTITIES:
            self.status_label.setText('A profile supports at most 50 job / education records.')
            return None
        if kind not in RECORD_KINDS:
            raise ValueError('Unsupported resume record kind.')
        eid = entity_id or uuid.uuid4().hex
        loading = self._loading
        self._loading = True
        row = self.entities_table.rowCount()
        self.entities_table.insertRow(row)
        kind_item = QTableWidgetItem(kind)
        kind_item.setFlags(kind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.entities_table.setItem(row, 0, kind_item)
        label_item = QTableWidgetItem(label or ('New job' if kind == 'experience' else 'New education'))
        label_item.setData(Qt.ItemDataRole.UserRole, eid)
        self.entities_table.setItem(row, 1, label_item)
        self.entities_table.setItem(row, 2, QTableWidgetItem(start_date))
        self.entities_table.setItem(row, 3, QTableWidgetItem(end_date))
        self._loading = loading
        self._refresh_entity_choices()
        self.refresh_groups()
        return eid

    def _entities(self):
        return [{'id': self.entities_table.item(row, 1).data(Qt.ItemDataRole.UserRole),
                 'kind': self.entities_table.item(row, 0).text(),
                 'label': self.entities_table.item(row, 1).text(),
                 'start_date': self.entities_table.item(row, 2).text().strip(),
                 'end_date': self.entities_table.item(row, 3).text().strip()}
                for row in range(self.entities_table.rowCount())]

    def _populate_entity_choice(self, choice, category, selected=None):
        choice.blockSignals(True)
        choice.clear()
        kind = category.split('.')[0]
        choice.addItem('Choose record…' if kind in RECORD_KINDS else 'Personal', None)
        for entity in self._entities():
            if entity['kind'] == kind:
                choice.addItem(entity['label'], entity['id'])
        choice.setCurrentIndex(max(0, choice.findData(selected)))
        choice.blockSignals(False)

    def _refresh_entity_choices(self):
        for row in range(self.facts_table.rowCount()):
            combo = self.facts_table.cellWidget(row, 2)
            self._populate_entity_choice(combo, self.facts_table.cellWidget(row, 1).currentData(), combo.currentData())

    def _entity_changed(self, item):
        if not self._loading:
            if item.column() == 1:
                self._refresh_entity_choices()
                self.refresh_groups()
            identity = self.entities_table.item(item.row(), 1)
            if identity is not None:
                eid = identity.data(Qt.ItemDataRole.UserRole)
                if item.column() in (2, 3):
                    key = self.entities_table.item(item.row(), 0).text() + ('.start_date' if item.column() == 2 else '.end_date')
                    self._loading = True
                    for row in range(self.facts_table.rowCount()):
                        if self.facts_table.cellWidget(row, 2).currentData() == eid and self.facts_table.cellWidget(row, 1).currentData() == key:
                            self.facts_table.item(row, 3).setText(item.text())
                            self.facts_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dict(method='manual', evidence=[]))
                    self._loading = False
                for row in range(self.facts_table.rowCount()):
                    if self.facts_table.cellWidget(row, 2).currentData() == eid:
                        self.facts_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)

    def add_fact(self, key='person.full_name', value='', entity_id=None, source='', reviewed=False, *, provenance=None):
        if self.facts_table.rowCount() >= MAX_FACTS:
            self.status_label.setText('A profile supports at most 300 facts.')
            return
        loading = self._loading
        self._loading = True
        row = self.facts_table.rowCount()
        self.facts_table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Checked if reviewed else Qt.CheckState.Unchecked)
        if provenance is not None:
            check.setData(Qt.ItemDataRole.UserRole, deepcopy(provenance))
        self.facts_table.setItem(row, 0, check)
        category = QComboBox()
        for category_key, label in CATEGORIES.items():
            category.addItem(label, category_key)
        category.setCurrentIndex(max(0, category.findData(key)))
        self.facts_table.setCellWidget(row, 1, category)
        entity = QComboBox()
        self._populate_entity_choice(entity, key, entity_id)
        self.facts_table.setCellWidget(row, 2, entity)
        self.facts_table.setItem(row, 3, QTableWidgetItem('\n'.join(value) if type(value) is list else value))
        self.facts_table.setItem(row, 4, QTableWidgetItem(source))
        category.currentIndexChanged.connect(lambda: self._category_changed(category))
        entity.currentIndexChanged.connect(lambda: self._choice_changed(entity))
        self._loading = loading
        if not loading:
            self.filter_group()

    def _category_changed(self, combo):
        if self._loading:
            return
        for row in range(self.facts_table.rowCount()):
            if self.facts_table.cellWidget(row, 1) is combo:
                self._populate_entity_choice(self.facts_table.cellWidget(row, 2), combo.currentData())
                self.facts_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                self.facts_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dict(method='manual', evidence=[]))
                break

    def _choice_changed(self, combo):
        if not self._loading:
            for row in range(self.facts_table.rowCount()):
                if self.facts_table.cellWidget(row, 2) is combo:
                    self.facts_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                    self.facts_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dict(method='manual', evidence=[]))

    def _fact_changed(self, item):
        if not self._loading and item.column() in (3, 4):
            self.facts_table.item(item.row(), 0).setCheckState(Qt.CheckState.Unchecked)
            self.facts_table.item(item.row(), 0).setData(Qt.ItemDataRole.UserRole, dict(method='manual', evidence=[]))
            if item.column() == 3:
                self.sync_fact_chronology(item.row())

    def sync_fact_chronology(self, row):
        key = self.facts_table.cellWidget(row, 1).currentData()
        eid = self.facts_table.cellWidget(row, 2).currentData()
        if not eid or not key.endswith(('.start_date', '.end_date')):
            return
        self._loading = True
        for entity_row in range(self.entities_table.rowCount()):
            if self.entities_table.item(entity_row, 1).data(Qt.ItemDataRole.UserRole) == eid:
                self.entities_table.item(entity_row, 2 if key.endswith('.start_date') else 3).setText(self.facts_table.item(row, 3).text())
        for fact_row in range(self.facts_table.rowCount()):
            if self.facts_table.cellWidget(fact_row, 2).currentData() == eid:
                self.facts_table.item(fact_row, 0).setCheckState(Qt.CheckState.Unchecked)
        self._loading = False

    def remove_selected_facts(self):
        for row in sorted({index.row() for index in self.facts_table.selectedIndexes()}, reverse=True):
            self.facts_table.removeRow(row)

    def remove_selected_entities(self):
        rows = sorted({index.row() for index in self.entities_table.selectedIndexes()}, reverse=True)
        eids = {self.entities_table.item(row, 1).data(Qt.ItemDataRole.UserRole) for row in rows}
        for row in reversed(range(self.facts_table.rowCount())):
            if self.facts_table.cellWidget(row, 2).currentData() in eids:
                self.facts_table.removeRow(row)
        for row in rows:
            self.entities_table.removeRow(row)
        self._refresh_entity_choices()
        self.refresh_groups()

    def _facts(self):
        facts = []
        for row in range(self.facts_table.rowCount()):
            key = self.facts_table.cellWidget(row, 1).currentData()
            value = self.facts_table.item(row, 3).text().strip()
            if key in LIST_CATEGORIES:
                value = [item.strip() for item in value.splitlines() if item.strip()]
            fact = {'key': key, 'value': value,
                'entity_id': self.facts_table.cellWidget(row, 2).currentData(),
                'source': self.facts_table.item(row, 4).text(),
                'reviewed': self.facts_table.item(row, 0).checkState() == Qt.CheckState.Checked}
            provenance = self.facts_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if provenance is not None:
                fact['provenance'] = deepcopy(provenance)
            facts.append(fact)
        return facts

    def refresh_groups(self):
        if not hasattr(self, 'group_filter') or not hasattr(self, 'entities_table'):
            return
        selected = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem('All resume facts', 'all')
        self.group_filter.addItem('Contact and professional profile', 'personal')
        self.group_filter.addItem('Skills and languages', 'skills')
        for entity in self._entities():
            self.group_filter.addItem(f'{entity["kind"].title()} · {entity["label"]}', entity['id'])
        index = self.group_filter.findData(selected)
        self.group_filter.setCurrentIndex(max(0, index))
        self.group_filter.blockSignals(False)
        self.filter_group()

    def filter_group(self, *_):
        if not hasattr(self, 'facts_table'):
            return
        selected = self.group_filter.currentData() or 'all'
        for row in range(self.facts_table.rowCount()):
            category = self.facts_table.cellWidget(row, 1)
            record = self.facts_table.cellWidget(row, 2)
            if category is None or record is None:
                continue
            key, eid = category.currentData(), record.currentData()
            visible = (selected == 'all' or selected == eid or
                selected == 'skills' and key in ('skills.items', 'languages.items') or
                selected == 'personal' and eid is None and key not in ('skills.items', 'languages.items'))
            self.facts_table.setRowHidden(row, not visible)

    def mark_group_reviewed(self):
        if self._worker or self._ai_worker:
            return
        self.review_group_button.setFocus()
        for row in range(self.facts_table.rowCount()):
            if not self.facts_table.isRowHidden(row):
                self.facts_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self.status_label.setText('Displayed values marked reviewed. Changes will require review again. Save explicitly to keep this profile.')

    def show_fact_evidence(self, row, *_):
        if self._loading or not hasattr(self, 'evidence_preview') or row < 0 or row >= self.facts_table.rowCount():
            return
        item = self.facts_table.item(row, 0)
        source = self.facts_table.item(row, 4)
        if item is None or source is None:
            return
        proof = item.data(Qt.ItemDataRole.UserRole) or {}
        if self._preview_worker is not None:
            self._preview_worker.cancelled.set()
            self._preview_worker = None
        self.page_image.clear()
        self.page_image.hide()
        self._preview_page = next((span.get('page') for span in proof.get('evidence', []) if span.get('page')), None)
        self._preview_source_fingerprint = proof.get('source_fingerprint')
        same_source = bool(self._document and proof.get('source_fingerprint') == self._document['fingerprint'])
        self.page_preview_button.setEnabled(bool(same_source and self._document['kind'] == 'pdf' and self._preview_page))
        lines = ['Source: ' + {'text': 'verified document text', 'visual': 'AI page transcription · confirm against the original PDF',
                 'manual': 'user-entered / edited'}.get(proof.get('method'), 'original excerpt / user review')]
        if proof.get('normalization') not in (None, 'identity'):
            lines.append('Normalization: ' + str(proof['normalization']))
        for span in proof.get('evidence', []):
            lines.append(f'Page / paragraph {span.get("page", "?")} · {span["block_id"]}\n{span["quote"]}')
        if not proof.get('evidence'):
            lines.append(source.text())
        if proof.get('evidence') and not same_source:
            lines.append('The original document for this saved evidence is not loaded. Page preview is unavailable.')
        self.evidence_preview.setPlainText('\n\n'.join(lines))

    def preview_selected_page(self):
        if (not self._document or self._document['kind'] != 'pdf' or not self._preview_page or self._preview_worker is not None
                or self._preview_source_fingerprint != self._document['fingerprint']):
            return
        worker = _PagePreviewWorker(self._document['raw_bytes'], self._preview_page)
        self._preview_worker = worker
        _IMPORT_WORKERS.add(worker)
        worker.succeeded.connect(self._page_previewed)
        worker.failed.connect(lambda: self.status_label.setText('Page preview unavailable. Check the original PDF separately.')
            if self._preview_worker is worker and not self._closed else None)
        worker.finished.connect(lambda: self._preview_finished(worker))
        worker.finished.connect(lambda: _IMPORT_WORKERS.discard(worker))
        worker.start()

    def _clear_page_preview(self):
        if self._preview_worker is not None:
            self._preview_worker.cancelled.set()
            self._preview_worker = None
        self._preview_page = self._preview_source_fingerprint = None
        if hasattr(self, 'page_preview_button'):
            self.page_preview_button.setEnabled(False)
            self.page_image.clear()
            self.page_image.hide()
        for preview in tuple(self._page_previews):
            for label in preview.findChildren(QLabel):
                label.clear()
            preview.close()
            preview.deleteLater()
        self._page_previews.clear()

    def _preview_finished(self, worker):
        if self._preview_worker is worker:
            self._preview_worker = None

    def _page_previewed(self, data):
        if self._closed or self.sender() is not self._preview_worker:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(data, 'PNG'):
            self.page_image.setPixmap(pixmap.scaled(650, 230, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self.page_image.show()
            # The inline thumbnail orients the user; the native-resolution scroll
            # view makes the actual source text readable for visual verification.
            preview = QDialog(self)
            preview.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            preview.setWindowTitle(f'Original resume PDF · page {self._preview_page}')
            preview.resize(850, 720)
            layout = QVBoxLayout(preview)
            scroll = QScrollArea()
            image = QLabel()
            image.setPixmap(pixmap)
            image.adjustSize()
            scroll.setWidget(image)
            layout.addWidget(scroll)
            close = QPushButton('Close page preview')
            close.clicked.connect(preview.close)
            layout.addWidget(close)
            preview.show()
            self._page_previews.add(preview)
            preview.finished.connect(lambda: self._page_previews.discard(preview))

    def use_replacement(self, index):
        if self._ai_worker or self._worker or not 0 <= index < len(self._replacements):
            return
        replacement = self._replacements[index]
        if replacement.get('resolved'):
            return
        proposed = replacement['proposed']
        for row in range(self.facts_table.rowCount()):
            if (self.facts_table.cellWidget(row, 1).currentData(), self.facts_table.cellWidget(row, 2).currentData()) == (proposed['key'], proposed['entity_id']):
                self._loading = True
                self.facts_table.item(row, 3).setText('\n'.join(proposed['value']) if isinstance(proposed['value'], list) else proposed['value'])
                self.facts_table.item(row, 4).setText(proposed['source'])
                self.facts_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, deepcopy(proposed.get('provenance')))
                self.facts_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                self._loading = False
                self.sync_fact_chronology(row)
                replacement['resolved'] = True
                self.extraction_summary._show_replacements()
                self.facts_table.setCurrentCell(row, 3)
                self.show_fact_evidence(row)
                self.status_label.setText('Replacement applied to the draft. Review this value again before saving.')
                break

    def save_profile(self):
        if self._closed or self._worker or self._ai_worker:
            return
        profile = deepcopy(self._profile)
        profile['name'] = self.name_edit.text().strip()
        profile['entities'] = self._entities()
        profile['facts'] = self._facts()
        try:
            self.saved_profile = self.store.save_profile(profile)
        except (ValueError, OSError) as exc:
            self.status_label.setText(str(exc))
            return
        self.profile_saved.emit(deepcopy(self.saved_profile))
        self._release_buffers()
        super().accept()

    def _release_buffers(self):
        self._closed = True
        self.cancel_import()
        self.source_edit.clear()
        self._loading = True
        self.facts_table.setRowCount(0)
        self.entities_table.setRowCount(0)
        self.name_edit.clear()
        self._profile = None
        self._document = None
        self._extraction_report = None
        self._replacements = []
        self.extraction_summary.clear()
        self.evidence_preview.clear()
        self.page_image.clear()
        self._preview_page = None
        for preview in tuple(self._page_previews):
            for label in preview.findChildren(QLabel):
                label.clear()
            preview.close()
            preview.deleteLater()
        self._page_previews.clear()

    def reject(self):
        if not self._closed:
            self._release_buffers()
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
