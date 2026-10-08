import threading
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QWidget, QGroupBox, QCheckBox, QComboBox,
                              QDoubleSpinBox, QFormLayout)
from lora_settings import loaders, available_files, request_config, changed_workflow, trigger_for


class LoraDialog(QDialog):
    completed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('LoRA settings')
        self.resize(720, 540)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.workflow = None
        self.controls = {}
        layout = QVBoxLayout(self)
        intro = QLabel('Choose how each LoRA influences your generated pictures.\nChanges apply to future images; they do not change training.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.cards = QVBoxLayout(self.body)
        scroll.setWidget(self.body)
        layout.addWidget(scroll, 1)
        self.message = QLabel('Loading current settings…')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        row.addWidget(close)
        row.addStretch()
        self.save = QPushButton('Save settings')
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_settings)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.completed.connect(self.on_completed)
        self.busy = True
        self.work(lambda: (request_config(), available_files()), 'load')

    def work(self, operation, kind):
        def run():
            try:
                result = operation()
            except Exception as error:
                result = error
            try:
                self.completed.emit(result, kind)
            except RuntimeError:
                pass  # Window closed while a read was in progress.
        threading.Thread(target=run, daemon=True).start()

    def on_completed(self, result, kind):
        self.busy = False
        if isinstance(result, Exception):
            self.message.setText(str(result))
            self.save.setEnabled(bool(self.controls))
            self.body.setEnabled(True)
            return
        if kind == 'save':
            self.workflow = result
            self.message.setText('Saved. These settings will be used for future images.')
            self.save.setEnabled(True)
            self.body.setEnabled(True)
            return
        self.workflow, self.filenames = result
        for key, node in loaders(self.workflow).items():
            inputs = node['inputs']
            name = inputs['lora_name']
            title = node.get('_meta', {}).get('title') or ('Personal LoRA' if name.startswith('personal_') else 'Additional LoRA')
            group = QGroupBox(title)
            form = QFormLayout(group)
            enabled = QCheckBox('Enabled')
            strength_value = float(inputs.get('strength_model', 0.8))
            enabled.setChecked(strength_value != 0)
            form.addRow(enabled)
            files = QComboBox()
            files.addItems(self.filenames)
            if name not in self.filenames:
                files.addItem(name)
            files.setCurrentText(name)
            form.addRow('LoRA file', files)
            strength = QDoubleSpinBox()
            strength.setRange(0, 2)
            strength.setSingleStep(0.05)
            strength.setDecimals(2)
            strength.setValue(strength_value if strength_value else 0.8)
            strength.setEnabled(enabled.isChecked())
            enabled.toggled.connect(strength.setEnabled)
            form.addRow('Strength', strength)
            hint = QLabel('Lower strength gives a lighter influence. Higher strength can exaggerate features.')
            hint.setWordWrap(True)
            form.addRow(hint)
            trigger = QLabel()
            trigger.setWordWrap(True)
            def update_trigger(filename, label=trigger):
                phrase = trigger_for(filename)
                label.setText('Use in your prompt: ' + phrase if phrase else 'No saved trigger phrase for this file.')
            files.currentTextChanged.connect(update_trigger)
            update_trigger(name)
            form.addRow(trigger)
            self.cards.addWidget(group)
            self.controls[key] = (enabled, files, strength)
        self.cards.addStretch()
        self.message.setText('Settings are shared with Image Studio on this server.' if self.controls else 'No LoRA loaders are present in the current image workflow.')
        self.save.setEnabled(bool(self.controls))

    def save_settings(self):
        changes = {key: {'filename': files.currentText(), 'strength': strength.value() if enabled.isChecked() else 0}
                   for key, (enabled, files, strength) in self.controls.items()}
        try:
            updated = changed_workflow(self.workflow, changes, self.filenames)
        except ValueError as error:
            self.message.setText(str(error))
            return
        original = self.workflow
        self.busy = True
        self.body.setEnabled(False)
        self.save.setEnabled(False)
        self.message.setText('Saving…')
        self.work(lambda: request_config('save', original=original, updated=updated), 'save')

    def closeEvent(self, event):
        if self.busy and self.workflow is not None:
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if not self.busy or self.workflow is None:
            super().reject()
