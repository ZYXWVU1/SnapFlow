"""Disposable Phase 10 integration check inside the frozen Windows application."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QEventLoop, QThreadPool, QTimer, Qt

from src.context.controller import ContextController
from src.context.models import ContextRequest, ContextSession
from src.context.permissions import ContextPermissionService
from src.context.retrieval import ContextRetrievalService
from src.memory.storage import MemoryStore
from src.paths import AppPaths
from src.skills.base import SkillResult
from src.ui.context_assistant import ContextAssistantDialog
from src.ui.memory_dialogs import MemoryDetailDialog


def _run_worker(session, request, store, client, permissions):
    from src.app import ContextWorker

    worker = ContextWorker(ContextController(store, client, permissions), session, request)
    outcomes = []
    loop = QEventLoop()

    def finished(request_id, result, error):
        outcomes.append((request_id, result, error))
        loop.quit()

    worker.signals.finished.connect(finished)
    QThreadPool.globalInstance().start(worker)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    if not outcomes:
        raise RuntimeError('Context worker timed out.')
    request_id, result, error = outcomes[0]
    if request_id != request.request_id or error:
        raise RuntimeError(error or 'Context worker returned the wrong request.')
    return result


def run_packaged_context_smoke(app):
    """Check packaged Qt, Memory, provenance, permissions, and worker wiring."""
    dialog = None
    try:
        with TemporaryDirectory(prefix='SnapFlow phase10 package - ') as directory:
            store = MemoryStore(AppPaths(data_dir=Path(directory)))
            saved = store.save_result(SkillResult('assignment', 'Assignment', 1.0,
                {'title': 'Synthetic saved assignment', 'deadline': '2026-10-05'}, []),
                title='Synthetic saved assignment')
            store.save_result(SkillResult('assignment', 'Assignment', 1.0,
                {'title': 'Unrelated private record'}, []),
                title='Unrelated private record')
            if saved.id not in [hit.memory_id for hit in store.search('Synthetic saved')]:
                raise RuntimeError('Packaged local Memory search failed.')

            dialog = ContextAssistantDialog(store)
            dialog.show()
            dialog.scope.setCurrentIndex(dialog.scope.findData('selected_memories'))
            for index in range(dialog.memory_list.count()):
                item = dialog.memory_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == saved.id:
                    item.setCheckState(Qt.CheckState.Checked)
                    break
            if dialog.selected != {saved.id}:
                raise RuntimeError('Packaged Memory selector failed.')

            submitted = []
            dialog.question_requested.connect(lambda question, scope, ids:
                                              submitted.append((question, scope, ids)))
            dialog.question.setText('Compare the synthetic deadlines')
            dialog.ask_button.click()
            if submitted != [('Compare the synthetic deadlines', 'selected_memories', (saved.id,))]:
                raise RuntimeError('Packaged contextual question was not scoped correctly.')

            session = ContextSession.new('synthetic-packaged-capture')
            permissions = ContextPermissionService()
            permissions.grant_selected(session, (saved.id,))
            current = {'title': 'Synthetic current assignment', 'deadline': '2026-10-12'}

            class FakeClient:
                def __init__(self):
                    self.prompts = []

                def request_text(self, prompt, **kwargs):
                    self.prompts.append(prompt)
                    return ('The current deadline is 2026-10-12 [C1]. '
                            'The saved deadline is 2026-10-05 [M1].'
                            if '[M1]' in prompt else 'The current deadline is 2026-10-12 [C1].')

            client = FakeClient()
            first = _run_worker(session, ContextRequest.new(session, submitted[0][0],
                current_skill_result=current), store, client, permissions)
            dialog.show_result(first)
            if (first.source_ids != ('C1', 'M1') or len(dialog.source_buttons) != 2 or
                    'Unrelated private record' in client.prompts[-1]):
                raise RuntimeError('Packaged selected-Memory answer or provenance failed.')

            opened = []
            dialog.memory_open_requested.connect(opened.append)
            dialog.source_buttons[1].click()
            if opened != [saved.id]:
                raise RuntimeError('Packaged source reference pointed to the wrong Memory.')
            MemoryDetailDialog(store, opened[0]).close()

            permissions.revoke_permission(session)
            dialog.revoke_button.click()
            second = _run_worker(session, ContextRequest.new(session, 'Current deadline?',
                current_skill_result=current), store, client, permissions)
            if (second.source_ids != ('C1',) or 'Synthetic saved assignment' in client.prompts[-1]
                    or 'Unrelated private record' in client.prompts[-1]):
                raise RuntimeError('Packaged current-only or revocation check failed.')

            permissions.grant_search(session)
            found = ContextRetrievalService(store, permissions).retrieve(session,
                ContextRequest.new(session, 'Synthetic saved assignment',
                    current_skill_result=current))
            if saved.id not in {record.id for record in found}:
                raise RuntimeError('Packaged authorized Memory retrieval failed.')
        app.exit(0)
    except Exception as exc:
        print(f'Phase 10 packaged smoke failed: {type(exc).__name__}: {exc}', flush=True)
        app.exit(1)
    finally:
        if dialog is not None:
            dialog.close()
