import unittest

from PySide6.QtWidgets import QApplication, QLineEdit

from src.ui.integrations.connection_dialog import GoogleConnectDialog, TodoistConnectDialog


APP = QApplication.instance() or QApplication([])


class ConnectionDialogTests(unittest.TestCase):
    def test_google_dialog_collects_client_id_and_chosen_capabilities(self):
        dialog = GoogleConnectDialog()
        received = []
        dialog.submitted.connect(lambda client, capabilities: received.append((client, capabilities)))
        try:
            dialog.client_id.setText('demo.apps.googleusercontent.com')
            dialog.calendar.setChecked(True)
            dialog.sheets.setChecked(False)
            dialog.connect_button.click()
            self.assertEqual(received, [('demo.apps.googleusercontent.com', ('google_calendar',))])
        finally:
            dialog.close()

    def test_todoist_token_is_masked_and_not_retained_after_submit(self):
        dialog = TodoistConnectDialog()
        received = []
        dialog.submitted.connect(received.append)
        try:
            self.assertEqual(dialog.token.echoMode(), QLineEdit.EchoMode.Password)
            dialog.token.setText('synthetic-token')
            dialog.connect_button.click()
            self.assertEqual(received, ['synthetic-token'])
            self.assertEqual(dialog.token.text(), '')
        finally:
            dialog.close()
