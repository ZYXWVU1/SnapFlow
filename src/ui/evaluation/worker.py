"""Keep paid evaluation requests off the Qt main thread."""
from PySide6.QtCore import QObject, QRunnable, Signal


class EvaluationSignals(QObject):
    finished = Signal(object, str)


class EvaluationWorker(QRunnable):
    def __init__(self, runner, version_id, dataset_id, model_config_id):
        super().__init__()
        self.runner, self.version_id = runner, version_id
        self.dataset_id, self.model_config_id = dataset_id, model_config_id
        self.signals = EvaluationSignals()

    def run(self):
        try:
            report = self.runner.evaluate(self.version_id, self.dataset_id, self.model_config_id)
            self.signals.finished.emit(report, '')
        except Exception as exc:
            self.signals.finished.emit(None, f'Evaluation failed: {type(exc).__name__}: {exc}')
