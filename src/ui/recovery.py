"""Small native recovery controls. No operation is replayed by this UI."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox


class RecoveryDialog(QDialog):
    def __init__(self, *, unclean=False, interrupted=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle('SnapFlow Recovery')
        self.mode = 'normal'
        layout = QVBoxLayout(self)
        self.message = QLabel(
            ('SnapFlow did not close normally last time.\n' if unclean else '') +
            f'{interrupted} interrupted or unknown write(s) need review.\n'
            'No actions have been retried. Check the destination before repeating an action.')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.normal_button = QPushButton('Start Normally')
        self.safe_button = QPushButton('Start in Safe Mode')
        self.diagnostics_button = QPushButton('Open Diagnostics')
        for button, mode in ((self.normal_button, 'normal'), (self.safe_button, 'safe'),
                             (self.diagnostics_button, 'diagnostics')):
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, choice=mode: self.choose(choice))
        self.normal_button.setDefault(True)

    def choose(self, mode):
        self.mode = mode
        self.accept()


class DatabaseRecoveryDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.retry = False
        self.setWindowTitle('SnapFlow Data Recovery')
        layout = QVBoxLayout(self)
        note = QLabel('The local database could not be opened safely. Existing files have been preserved. '
                      'Normal writes are stopped. Restore a verified backup or export the damaged file for recovery.')
        note.setWordWrap(True)
        layout.addWidget(note)
        for title, callback in (('Retry', self._retry), ('Restore Backup', self._restore),
                                ('Open Diagnostics', self._diagnostics), ('Export Recoverable Data', self._export)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def _retry(self):
        self.retry = True
        self.accept()

    def _restore(self):
        from src.backup import BackupService
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose Backup', '', 'SnapFlow Backup (*.zip)')
        if not filename:
            return
        try:
            service = BackupService(self.paths)
            service.inspect_backup(filename)
            if QMessageBox.question(self, 'Restore Backup', 'Replace current data with this verified backup? '
                    'A rollback backup will be created first.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            from src.reliability.database_recovery import restore_after_corruption
            restore_after_corruption(self.paths, filename)
            self._retry()
        except Exception:
            QMessageBox.warning(self, 'Recovery unavailable', 'Restore could not complete safely. Existing data was preserved.')

    def _diagnostics(self):
        from src.diagnostics import DiagnosticsService
        result = DiagnosticsService(self.paths).run_basic_health_check()
        QMessageBox.information(self, 'Local Diagnostics',
            f'Database: {result.database_status}\nData writable: {result.data_writable}\n'
            f'Logs writable: {result.logs_writable}\nNo data was uploaded.')

    def _export(self):
        from src.reliability.database_recovery import export_damaged_database
        filename, _ = QFileDialog.getSaveFileName(self, 'Export Damaged Database', '', 'SQLite (*.sqlite3)')
        if not filename:
            return
        try:
            export_damaged_database(self.paths.learning_database, filename)
            QMessageBox.information(self, 'Exported', 'A local copy was saved for manual recovery. It contains user data; share only deliberately.')
        except Exception:
            QMessageBox.warning(self, 'Export unavailable', 'The database could not be copied. Existing data was preserved.')
