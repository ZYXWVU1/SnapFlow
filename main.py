"""Windows desktop entry point."""
import sys

from src.bootstrap import DependencyError, ensure_dependencies, show_dependency_error


def main() -> int:
    try:
        ensure_dependencies()
    except DependencyError as error:
        show_dependency_error(error)
        return 1

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from src.app import ApplicationController
    app = QApplication(sys.argv)
    app.setApplicationName("AI Screenshot Helper")
    app.setQuitOnLastWindowClosed(False)
    controller = ApplicationController(app, preview="--preview" in sys.argv)
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(500, controller.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
