"""Windows desktop entry point."""
import logging
import os
import sys

from src.bootstrap import DependencyError, ensure_dependencies, show_dependency_error
from src.app_version import APP_NAME, APP_VERSION


def main() -> int:
    phase10_smoke = ("--phase10-smoke-test" in sys.argv and
                     os.environ.get("SNAPFLOW_PHASE10_SMOKE") == "1")
    try:
        ensure_dependencies()
    except DependencyError as error:
        if "--smoke-test" in sys.argv or phase10_smoke:
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
        MigrationCoordinator(paths).run()
        if paths.learning_database.exists():
            initialize_learning_database(paths.learning_database, paths.backup_dir)
    except (DatabaseMigrationError, MigrationError, RuntimeError, OSError) as error:
        instance_guard.close()
        report = classify_error(error)
        logging.getLogger(__name__).exception(
            "Application startup failed [%s] category=%s", report.reference_id, report.category)
        if "--smoke-test" in sys.argv or phase10_smoke:
            print(f"Startup smoke failed [{report.reference_id}]: {report.user_message}", file=sys.stderr)
            return 1
        QMessageBox.critical(
            None,
            APP_NAME,
            f"{report.user_message}\n\nReference: {report.reference_id}\n"
            "Existing files were left recoverable. Check the data folder and try again.",
        )
        return 1

    app.aboutToQuit.connect(instance_guard.close)
    controller = ApplicationController(
        app,
        preview="--preview" in sys.argv,
        paths=paths,
        suppress_onboarding="--smoke-test" in sys.argv or phase10_smoke)
    if phase10_smoke:
        from src.context.packaged_smoke import run_packaged_context_smoke
        QTimer.singleShot(0, lambda: run_packaged_context_smoke(app))
    elif "--smoke-test" in sys.argv:
        QTimer.singleShot(500, controller.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
