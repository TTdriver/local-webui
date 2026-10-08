"""Regression coverage for QDialog close/reject reentrancy and save guards."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from image_dialog import ImageDialog
from lora_dialog import LoraDialog


class DialogCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, kind):
        with patch.object(kind, 'work'):
            dialog = kind()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dialog.busy = False
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_close_button_escape_and_window_close(self):
        for kind in (ImageDialog, LoraDialog):
            for action in ('button', 'escape', 'window'):
                with self.subTest(dialog=kind.__name__, action=action):
                    dialog = self.dialog(kind)
                    if action == 'button':
                        next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Close').click()
                    elif action == 'escape':
                        QTest.keyClick(dialog, Qt.Key.Key_Escape)
                    else:
                        dialog.close()
                    self.assertFalse(dialog.isVisible())
                    dialog.deleteLater()

    def test_save_cannot_be_interrupted(self):
        for kind in (ImageDialog, LoraDialog):
            with self.subTest(dialog=kind.__name__):
                dialog = self.dialog(kind)
                if kind is ImageDialog:dialog.saving = True
                else:dialog.busy = True;dialog.workflow = {}
                dialog.close()
                self.assertTrue(dialog.isVisible())
                QTest.keyClick(dialog, Qt.Key.Key_Escape)
                self.assertTrue(dialog.isVisible())
                if kind is ImageDialog:dialog.saving = False
                else:dialog.busy = False
                dialog.close()
                self.assertFalse(dialog.isVisible())
                dialog.deleteLater()


if __name__ == '__main__':unittest.main()
