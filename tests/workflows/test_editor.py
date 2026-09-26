import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication
from src.skills.custom.models import CustomSkillDefinition, CustomFieldDefinition
from src.skills.custom.storage import CustomSkillStorage
from src.skills.registry import SkillRegistry
from src.workflows.storage import WorkflowStorage
from src.ui.workflows.workflow_editor import WorkflowEditor
from src.ui.workflows.workflow_manager import WorkflowManager

APP = QApplication.instance() or QApplication([])


class WorkflowEditorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.skills = CustomSkillStorage(Path(self.folder.name) / 'skills.json')
        self.registry = SkillRegistry(self.skills)
        self.storage = WorkflowStorage(Path(self.folder.name) / 'workflows.json')

    def test_create_builtin_and_reorder_with_stable_ids(self):
        manager = WorkflowManager(self.storage, self.registry)
        self.addCleanup(manager.close)
        editor = manager.create_workflow()
        editor.name.setText('Assignment summary')
        editor.trigger.setCurrentIndex(editor.trigger.findData('assignment'))
        editor.add_condition()
        editor.condition_rows[0][0].setCurrentIndex(editor.condition_rows[0][0].findData('due_date'))
        editor.condition_rows[0][1].setCurrentIndex(editor.condition_rows[0][1].findData('exists'))
        editor.add_step('copy_markdown')
        editor.add_step('copy_details')
        ids = [step.id for step in editor.steps]
        editor.step_list.setCurrentRow(1)
        editor.move_step(-1)
        self.assertEqual([step.id for step in editor.steps], ids[::-1])
        editor.submit()
        self.assertEqual(len(self.storage.list_workflows()), 1)
        self.assertEqual([step.id for step in self.storage.list_workflows()[0].steps], ids[::-1])

    def test_custom_schema_and_required_config(self):
        self.skills.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'Receipt screenshot',
            (CustomFieldDefinition('total', 'Total', 'number'),), ('save_csv', 'copy_json')))
        editor = WorkflowEditor(self.registry)
        self.addCleanup(editor.close)
        editor.name.setText('Save receipt')
        editor.trigger.setCurrentIndex(editor.trigger.findData('receipt'))
        editor.add_condition()
        fields, operators, value, _ = editor.condition_rows[0]
        self.assertEqual([fields.itemData(i) for i in range(fields.count())], ['total'])
        fields.setCurrentIndex(0)
        self.assertGreaterEqual(operators.findData('greater_than'), 0)
        operators.setCurrentIndex(operators.findData('greater_than'))
        value.setText('5')
        editor.add_step('save_csv')
        editor.submit()
        self.assertTrue(editor.error.text())
        editor.config_inputs['file_path'].setText(str(Path(self.folder.name) / 'receipt.csv'))
        editor.submit()
        self.assertEqual(editor.definition().steps[0].config['file_path'], str(Path(self.folder.name) / 'receipt.csv'))

    def test_manager_duplicate_import_export_and_preview(self):
        from unittest.mock import patch
        from src.skills.registry import SKILLS
        from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
        from src.workflows.import_export import export_workflow
        original = WorkflowDefinition('w', 'Summary', WorkflowTrigger('skill_match', 'assignment'),
            (WorkflowStep('s', 'copy_markdown'),), auto_run=True)
        self.storage.create_workflow(original)
        manager = WorkflowManager(self.storage, self.registry, allow_auto=True,
            result_provider=lambda: SKILLS['assignment'].parse('{"title":"HW"}'))
        self.addCleanup(manager.close)
        manager.list.setCurrentRow(0)
        manager.duplicate_selected()
        self.assertEqual(len(self.storage.list_workflows()), 2)
        duplicate = self.storage.list_workflows()[1]
        self.assertFalse(duplicate.auto_run)
        self.assertFalse(duplicate.enabled)
        self.assertNotEqual(duplicate.id, original.id)
        path = Path(self.folder.name) / 'summary.aiworkflow'
        manager.list.setCurrentRow(0)
        with patch('src.ui.workflows.workflow_manager.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            manager.export_selected()
        self.assertIn('ai-screenshot-helper-workflow', path.read_text(encoding='utf-8'))
        with patch('src.ui.workflows.workflow_manager.QFileDialog.getOpenFileName', return_value=(str(path), '')):
            manager.import_file()
        self.assertEqual(len(self.storage.list_workflows()), 3)
        self.assertFalse(self.storage.list_workflows()[2].auto_run)
        manager.list.setCurrentRow(0)
        with patch('src.ui.workflows.workflow_manager.QMessageBox.information') as shown:
            manager.test_selected()
        self.assertTrue(shown.called)
        self.assertIn('HW', shown.call_args.args[2])

    def test_switching_trigger_keeps_condition_valid(self):
        self.skills.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'Receipt',
            (CustomFieldDefinition('total', 'Total', 'number'),), ('copy_json',)))
        editor = WorkflowEditor(self.registry)
        self.addCleanup(editor.close)
        editor.name.setText('Changed trigger')
        editor.trigger.setCurrentIndex(editor.trigger.findData('assignment'))
        editor.add_condition()
        editor.condition_rows[0][0].setCurrentIndex(editor.condition_rows[0][0].findData('course'))
        editor.trigger.setCurrentIndex(editor.trigger.findData('receipt'))
        editor.add_step('copy_json')
        self.assertEqual(editor.condition_rows[0][0].currentData(), 'total')
        editor.submit()
        self.assertFalse(editor.error.text())

    def test_boolean_condition_can_be_saved(self):
        self.skills.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'Receipt',
            (CustomFieldDefinition('paid', 'Paid', 'boolean'),), ('copy_json',)))
        editor = WorkflowEditor(self.registry)
        self.addCleanup(editor.close)
        editor.name.setText('Paid receipt')
        editor.trigger.setCurrentIndex(editor.trigger.findData('receipt'))
        editor.add_condition()
        fields, operators, value, _ = editor.condition_rows[0]
        operators.setCurrentIndex(operators.findData('equals'))
        value.setText('true')
        editor.add_step('copy_json')
        self.assertIs(editor.definition().conditions[0].value, True)

    def test_action_config_survives_step_selection(self):
        editor = WorkflowEditor(self.registry)
        self.addCleanup(editor.close)
        editor.name.setText('Files')
        editor.trigger.setCurrentIndex(editor.trigger.findData('assignment'))
        editor.add_step('append_csv')
        editor.config_inputs['file_path'].setText('C:/data/jobs.csv')
        editor.add_step('copy_markdown')
        editor.step_list.setCurrentRow(0)
        self.assertEqual(editor.config_inputs['file_path'].text(), 'C:/data/jobs.csv')
        self.assertEqual(editor.definition().steps[0].config['file_path'], 'C:/data/jobs.csv')

    def test_missing_skill_cannot_be_enabled_from_manager(self):
        from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
        self.storage.create_workflow(WorkflowDefinition('orphan', 'Orphan',
            WorkflowTrigger('skill_match', 'missing'), (WorkflowStep('s', 'copy_json'),), enabled=False))
        manager = WorkflowManager(self.storage, self.registry)
        self.addCleanup(manager.close)
        manager.list.setCurrentRow(0)
        manager.toggle_selected()
        self.assertFalse(self.storage.get_workflow('orphan').enabled)
        self.assertIn('missing', manager.status.text().lower())

    def test_cloud_action_editor_uses_skill_field_selectors_and_column_mapping(self):
        editor = WorkflowEditor(self.registry)
        self.addCleanup(editor.close)
        editor.name.setText('Assignment to Sheets')
        editor.trigger.setCurrentIndex(editor.trigger.findData('assignment'))
        editor.add_step('google_sheets_append_row')
        columns = editor.config_inputs['columns']
        self.assertGreater(columns.field_options().index('title'), -1)
        editor.config_inputs['spreadsheet_id'].setText('spreadsheet123456')
        editor.config_inputs['tab'].setText('Assignments')
        columns.set_rows([{'column': 'Title', 'field': 'title'}])
        config = editor.definition().steps[0].config
        self.assertEqual(config['columns'], [{'column': 'Title', 'field': 'title'}])
        self.assertFalse(editor.error.text())

    def test_cloud_editor_offers_connection_from_step(self):
        class Connections:
            def get(self, key):
                return None
        editor = WorkflowEditor(self.registry, connection_storage=Connections())
        self.addCleanup(editor.close)
        requested = []
        editor.connect_requested.connect(requested.append)
        editor.add_step('todoist_create_task')
        self.assertIn('not connected', editor.integration_status.text().lower())
        editor.integration_connect.click()
        self.assertEqual(requested, ['todoist'])
