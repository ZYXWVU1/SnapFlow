"""Disposable acceptance inside the source/frozen app, with a real stdio bridge."""
import json
from pathlib import Path
import sys
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QMessageBox
from src.mcp.client.models import MCPConnectionProfile
from src.skills.base import SkillResult
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger


def _wait(future):
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if future.done() else None)
    poll.start()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(20000)
    if not future.done():
        loop.exec()
    poll.stop()
    deadline.stop()
    if not future.done():
        future.cancel()
        raise RuntimeError('Packaged MCP request timed out.')
    return future.result()


def run_packaged_mcp_smoke(app, controller):
    code = 0
    manager = None
    reject_timer = QTimer()
    try:
        parent = controller.paths.data_dir.parent
        if not parent.name.startswith('SnapFlow phase11 ') or not (parent / '.snapflow-phase11-test').is_file():
            raise RuntimeError('MCP smoke requires a disposable marked profile.')
        service = controller.server_service
        if service.policy.snapshot().enabled:
            raise RuntimeError('Fresh profiles must have the server disabled.')
        service.configure(enabled=True, share_memory=True, share_workflows=True, share_history=True)
        if not service.ipc.listener.isListening():
            raise RuntimeError('Current-user IPC failed to start.')
        saved = controller.memory_store.save_result(SkillResult('assignment', 'Assignment', 1.0,
            {'title': 'Synthetic MCP memory 测試', 'deadline': '2026-10-05'}, []), title='Synthetic MCP memory 测試')
        workflow = WorkflowDefinition('mcp_smoke', 'Synthetic MCP workflow', WorkflowTrigger('skill_match', 'code_error'),
            (WorkflowStep('s1', 'copy_error', {}),))
        controller.workflow_storage.create_workflow(workflow)
        if getattr(sys, 'frozen', False):
            executable, args = str(Path(sys.executable).with_name('SnapFlowMCP.exe')), ()
        else:
            executable, args = sys.executable, ('-m', 'src.mcp.server.bridge')
        profile = MCPConnectionProfile('packaged_smoke', 'Synthetic packaged bridge', executable=executable,
            args=args, working_directory=str(controller.paths.resource_root), enabled=True)
        controller.extension_service.storage.save_profile(profile)
        try:
            _wait(controller.extension_service.manager.connect(profile.id))
        except ValueError as error:
            if 'this SnapFlow instance. Connection blocked.' not in str(error):
                raise
        else:
            raise RuntimeError('Same-instance client connection was not blocked.')
        # Model an external host with its own isolated client metadata. The app's
        # actual client above must refuse the same-instance connection.
        from src.mcp.client.manager import MCPClientManager
        from src.mcp.client.storage import MCPStorage
        host_storage = MCPStorage(controller.paths.data_dir / 'acceptance_host' / 'connections.json')
        host_storage.save_profile(profile)
        manager = MCPClientManager(host_storage, controller.extension_service.credentials)
        state = _wait(manager.connect(profile.id))
        if not state.protocol_version or state.server_name != 'SnapFlow':
            raise RuntimeError('Bundled SDK initialization failed.')
        tools = {t.name: t for t in manager.snapshot(profile.id).tools}
        if 'delete_workflow' in tools or len(tools) != 10:
            raise RuntimeError('Unexpected server tool surface.')
        for name, tool in tools.items():
            manager.permissions.review(profile.id, name, tool.fingerprint,
                risk='external_write' if name == 'run_workflow' else 'read_only', mode='allowed')
        def call(name, **arguments):
            return _wait(manager.call_tool(profile.id, name, arguments, approved=True,
                expected_fingerprint=tools[name].fingerprint))
        if not call('list_visual_skills').success:
            raise RuntimeError('Bundled read-only service failed.')
        search = call('search_visual_memory', query='Synthetic MCP memory')
        if not search.success or saved.id not in search.text:
            raise RuntimeError('Bundled Memory search failed.')
        source = _wait(manager.read_resource_template(profile.id, 'snapflow://memory/{memory_id}', {'memory_id': saved.id}))
        if 'Synthetic MCP memory' not in source.content:
            raise RuntimeError('Bundled selected resource failed.')
        from src.context.models import ContextSession
        from src.context.permissions import ContextPermissionService
        session, permissions = ContextSession.new('synthetic-capture'), ContextPermissionService()
        permissions.select_external_sources(session, (source,))
        if session.scope != 'current_only':
            raise RuntimeError('External resource expanded Memory permissions.')
        permissions.revoke_permission(session)
        if session.external_sources:
            raise RuntimeError('External source revocation failed.')
        preview = call('preview_workflow', workflow_id=workflow.id, structured_context={'error_message': 'Synthetic error'})
        if not preview.success or controller.workflow_history.list_entries():
            raise RuntimeError('Bundled Workflow preview had effects or failed.')
        service.configure(allow_workflow_execution=True)
        approval_parent = service.approval_parent
        service.approval_parent = None
        if call('run_workflow', workflow_id=workflow.id, structured_context={'error_message': 'Synthetic error'}).success:
            raise RuntimeError('Headless run bypassed approval.')
        service.approval_parent = approval_parent
        rejections = []
        def reject():
            box = app.activeModalWidget()
            if isinstance(box, QMessageBox) and box.windowTitle() == 'External MCP Workflow request':
                rejections.append(True)
                box.reject()
        reject_timer.setInterval(10)
        reject_timer.timeout.connect(reject)
        reject_timer.start()
        if call('run_workflow', workflow_id=workflow.id, structured_context={'error_message': 'Synthetic error'}).success or not rejections:
            raise RuntimeError('Native approval rejection did not block execution.')
        if controller.workflow_history.list_entries():
            raise RuntimeError('Rejected Workflow produced history/effects.')
        service.configure(share_memory=False)
        if call('get_memory_record', memory_id=saved.id).success:
            raise RuntimeError('Memory revocation failed.')
        _wait(manager.disconnect(profile.id))
        controller.paths.data_dir.joinpath('phase11_smoke_ok.json').write_text(json.dumps({
            'protocol': state.protocol_version, 'tools': len(tools), 'bridge': Path(executable).name,
            'memory': True, 'resource': True, 'preview': True, 'headless_denied': True, 'approval_rejected': True,
            'revocation': True, 'self_connection_blocked': True}), encoding='utf-8')
    except Exception as error:
        code = 1
        controller.paths.data_dir.joinpath('phase11_smoke_error.txt').write_text(
            f'{type(error).__name__}: {str(error)[:500]}', encoding='utf-8')
    finally:
        reject_timer.stop()
        controller.server_service.close()
        if manager is not None:
            manager.close()
        controller.extension_service.manager.close()
        controller.hotkeys.close()
        controller.tray.hide()
        app.exit(code)
