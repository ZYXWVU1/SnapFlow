"""Windows desktop entry point."""
import logging
import os
import sys
import time

from src.bootstrap import DependencyError, ensure_dependencies, show_dependency_error
from src.app_version import APP_NAME, APP_VERSION
from src.startup_smoke import SmokeTests


def main() -> int:
    if len(sys.argv)>1 and sys.argv[1]=='--resume-import-helper':
        if len(sys.argv)<3 or sys.argv[2] not in ('pdf','docx'):
            return 2
        from src.resume.importer import run_import_helper
        if len(sys.argv)==3:
            return run_import_helper(sys.argv[2])
        if len(sys.argv)==4 and sys.argv[3]=='--document':
            return run_import_helper(sys.argv[2], document=True)
        if (len(sys.argv)==5 and sys.argv[2:4]==['pdf','--render-page'] and
                sys.argv[4].isdigit() and len(sys.argv[4])<=2):
            return run_import_helper('pdf', render_page=int(sys.argv[4]))
        return 2
    if '--browser-host-setup' in sys.argv:
        from src.browser.setup import main as browser_setup_main
        index = sys.argv.index('--browser-host-setup')
        try:
            return browser_setup_main(sys.argv[index + 1:])
        except (OSError, ValueError) as error:
            if sys.stderr is not None:
                print(f'Browser host setup did not complete [{type(error).__name__}]. '
                      'Existing registrations were preserved.', file=sys.stderr)
            return 1
    if '--mcp-server-stdio' in sys.argv or (getattr(sys, 'frozen', False) and
            os.path.basename(sys.executable).casefold() == 'snapflowmcp.exe'):
        from src.mcp.server.bridge import main as bridge_main
        return bridge_main()
    smoke_tests = SmokeTests()
    if not smoke_tests.prepare_startup():
        return 1
    smoke = smoke_tests.enabled
    try:
        ensure_dependencies()
    except DependencyError as error:
        if smoke:
            print(f"Startup smoke failed: {error}", file=sys.stderr)
            return 1
        show_dependency_error(error)
        return 1

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from src.app import ApplicationController
    from src.database import DatabaseMigrationError, initialize_learning_database
    from src.diagnostics import classify_error, configure_logging
    from src.migration import MigrationCoordinator, MigrationError
    from src.paths import AppPaths
    from src.single_instance import SingleInstanceGuard

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    paths = AppPaths()
    instance_guard = SingleInstanceGuard(paths)
    try:
        configure_logging(paths)
        if not instance_guard.acquire():
            return 0
        database_started = time.perf_counter()
        MigrationCoordinator(paths).run()
        migration_ms = (time.perf_counter() - database_started) * 1000
        database_started = time.perf_counter()
        if paths.learning_database.exists():
            initialize_learning_database(paths.learning_database, paths.backup_dir)
        database_ms = (time.perf_counter() - database_started) * 1000
    except (DatabaseMigrationError, MigrationError, RuntimeError, OSError) as error:
        report = classify_error(error)
        logging.getLogger(__name__).exception(
            "Application startup failed [%s] category=%s", report.reference_id, report.category)
        if smoke:
            instance_guard.close()
            print(f"Startup smoke failed [{report.reference_id}]: {report.user_message}", file=sys.stderr)
            return 1
        if isinstance(error, DatabaseMigrationError):
            from src.ui.recovery import DatabaseRecoveryDialog
            while True:
                recovery = DatabaseRecoveryDialog(paths)
                recovery.exec()
                if not recovery.retry:
                    instance_guard.close()
                    return 1
                try:
                    initialize_learning_database(paths.learning_database, paths.backup_dir)
                    database_ms = (time.perf_counter() - database_started) * 1000
                    break
                except DatabaseMigrationError:
                    continue
        else:
            instance_guard.close()
            QMessageBox.critical(
            None,
            APP_NAME,
            f"{report.user_message}\n\nReference: {report.reference_id}\n"
            "Existing files were left recoverable. Check the data folder and try again.",
            )
            return 1

    app.aboutToQuit.connect(instance_guard.close)
    safe_mode = '--safe-mode' in sys.argv
    diagnostics_requested = False
    if not smoke:
        unclean = False
        interrupted = 0
        try:
            if paths.observability_database.exists():
                from src.observability.store import LocalEventStore
                sessions = LocalEventStore(paths.observability_database).sessions()
                unclean = bool(sessions and sessions[0]['status'] == 'active')
            if paths.recovery_database.exists():
                from src.reliability.journal import ExecutionJournal
                journal = ExecutionJournal(paths.recovery_database)
                interrupted = sum(row['status'] in ('planned', 'approved', 'started', 'action_sent', 'verification_started', 'interrupted', 'unknown')
                                  for row in journal.records(10000))
        except Exception:
            logging.getLogger(__name__).warning('Previous reliability state could not be inspected safely.')
        if unclean or interrupted:
            from src.ui.recovery import RecoveryDialog
            recovery = RecoveryDialog(unclean=unclean, interrupted=interrupted)
            recovery.exec()
            safe_mode = safe_mode or recovery.mode in ('safe', 'diagnostics')
            diagnostics_requested = recovery.mode == 'diagnostics'
    try:
        controller = ApplicationController(
            app, preview="--preview" in sys.argv, paths=paths,
            suppress_onboarding=smoke, safe_mode=safe_mode)
    except Exception as error:
        instance_guard.close()
        try:
            from src.reliability.crashes import CrashReporter
            from src.config import load_config
            record = CrashReporter(paths.recovery_database,
                enabled=load_config(paths.config_file).local_crash_reports_enabled).record(error)
            message = record['safe_message'] + ' Reference: ' + record['crash_id'][:12]
        except Exception:
            message = 'Startup could not finish. Existing data was preserved. Try Safe Mode.'
        if smoke:
            print(message, file=sys.stderr)
        else:
            QMessageBox.critical(None, APP_NAME, message)
        return 1
    controller.crash_reporter.install()
    controller.profiler.record('startup_database', 'application', database_ms, True)
    controller.profiler.record('startup_migration', 'application', migration_ms, True)
    app.aboutToQuit.connect(controller.crash_reporter.uninstall)
    if diagnostics_requested:
        QTimer.singleShot(0, lambda: (controller.main_window.open_page('settings'), controller.main_window.show()))
    smoke_tests.schedule(app, controller)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
