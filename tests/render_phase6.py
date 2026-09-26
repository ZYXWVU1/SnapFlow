"""Render synthetic Phase 6 UI samples without accounts or external requests."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from src.skills.registry import SKILLS
from src.ui.design.theme import ThemeManager
from src.ui.main_window import MainWindow
from src.ui.result_window import ResultWindow
from src.integrations.registry import IntegrationRegistry
from src.ui.skills.skill_editor import SkillEditor
from src.ui.workflows.workflow_editor import WorkflowEditor
from src.skills.custom.storage import CustomSkillStorage
from src.skills.registry import SkillRegistry


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme = ThemeManager(app, 'light')
    suffix = '-150' if os.environ.get('QT_SCALE_FACTOR') == '1.5' else ''
    output = Path('docs/screenshots') if suffix else Path('assets/screenshots')
    output.mkdir(parents=True, exist_ok=True)
    class DemoSkills:
        def list_skills(self):
            return [SimpleNamespace(id='internship', name='Internship Tracker',
                description='Recognize internship postings', enabled=True,
                fields=[1, 2, 3], actions=['copy_json'])]
    class DemoWorkflows:
        def list_workflows(self):
            return [SimpleNamespace(id='save_internship', name='Save Internship',
                trigger=SimpleNamespace(skill_id='internship'), enabled=True, auto_run=False,
                steps=[SimpleNamespace(action_id='google_sheets_append_row'),
                       SimpleNamespace(action_id='todoist_create_task')])]
    window = MainWindow('ctrl+shift+s', skills_storage=DemoSkills(),
        workflows_storage=DemoWorkflows(), integration_registry=IntegrationRegistry())
    window.resize(1020, 820)
    window.show()
    app.processEvents()
    window.grab().save(str(output / f'home-light{suffix}.png'))
    window.open_page('integrations')
    app.processEvents()
    window.grab().save(str(output / f'integrations-light{suffix}.png'))
    window.open_page('skills')
    app.processEvents()
    window.grab().save(str(output / f'skills-light{suffix}.png'))
    window.open_page('workflows')
    app.processEvents()
    window.grab().save(str(output / f'workflows-light{suffix}.png'))
    theme.set_preference('dark')
    window.open_page('home')
    app.processEvents()
    window.grab().save(str(output / f'home-dark{suffix}.png'))
    window.close()
    result = ResultWindow('smart', False)
    result.set_skill_result(SKILLS['assignment'].parse('{"title":"Homework 5","course":"CS 220","due_date":"2026-09-30"}'))
    result.show()
    app.processEvents()
    result.grab().save(str(output / f'result-dark{suffix}.png'))
    result.close()
    theme.set_preference('light')
    skill_editor = SkillEditor()
    skill_editor.show()
    app.processEvents()
    skill_editor.grab().save(str(output / f'skill-editor-light{suffix}.png'))
    skill_editor.close()
    with tempfile.TemporaryDirectory() as folder:
        workflow_editor = WorkflowEditor(SkillRegistry(CustomSkillStorage(Path(folder) / 'skills.json')))
        workflow_editor.show()
        app.processEvents()
        workflow_editor.grab().save(str(output / f'workflow-editor-light{suffix}.png'))
        workflow_editor.close()


if __name__ == '__main__':
    main()
