import threading
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel, QPushButton, QComboBox
from image_settings import request_settings, FIELDS
from lora_dialog import LoraDialog


class ImageDialog(QDialog):
    completed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Image Settings')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(620, 410)
        self.state = None
        self.busy = True
        self.saving = False
        self.lora_dialog = None
        layout = QVBoxLayout(self)
        intro = QLabel('Choose the image models used by Image Studio and Image Editor. Settings apply to future images on this server.')
        intro.setWordWrap(True);layout.addWidget(intro)
        models = QGroupBox('Image models');form = QFormLayout(models)
        self.selectors = {}
        for slot, label in [('studio', 'Image Studio'), ('editor', 'Image Editor')]:
            combo = QComboBox();combo.setEnabled(False)
            form.addRow(label, combo);self.selectors[slot] = combo
            combo.currentIndexChanged.connect(self.update_lora)
        layout.addWidget(models)
        group = QGroupBox('LoRA settings');row = QVBoxLayout(group)
        self.lora_hint = QLabel();self.lora_hint.setWordWrap(True);row.addWidget(self.lora_hint)
        self.lora_button = QPushButton('Open LoRA settings')
        self.lora_button.setEnabled(False);self.lora_button.clicked.connect(self.open_lora);row.addWidget(self.lora_button)
        layout.addWidget(group)
        self.message = QLabel('Loading installed models…');self.message.setWordWrap(True);layout.addWidget(self.message)
        layout.addStretch()
        buttons = QHBoxLayout();close = QPushButton('Close');close.clicked.connect(self.close);buttons.addWidget(close);buttons.addStretch()
        self.save = QPushButton('Save settings');self.save.setEnabled(False);self.save.clicked.connect(self.save_settings);buttons.addWidget(self.save);layout.addLayout(buttons)
        self.completed.connect(self.on_completed)
        self.work(lambda: request_settings(), 'load')

    def work(self, operation, kind):
        def run():
            try:result = operation()
            except Exception as error:result = error
            try:self.completed.emit(result, kind)
            except RuntimeError:pass
        threading.Thread(target=run, daemon=True).start()

    def on_completed(self, result, kind):
        self.busy = False
        self.saving = False
        if isinstance(result, Exception):
            self.message.setText(str(result))
            self.save.setEnabled(self.state is not None)
            for combo in self.selectors.values():combo.setEnabled(self.state is not None)
            self.update_lora();return
        self.state = result
        for slot, combo in self.selectors.items():
            combo.blockSignals(True);combo.clear()
            for name, entry in result['catalog'][slot].items():combo.addItem(entry['label'], name)
            selected = result['current'].get(FIELDS[slot][0]);idx = combo.findData(selected)
            combo.setCurrentIndex(idx);combo.blockSignals(False);combo.setEnabled(True)
        self.save.setEnabled(all(c.currentIndex() >= 0 for c in self.selectors.values()))
        self.message.setText('Saved. The selected models will load when you next create or edit an image.' if kind == 'save' else 'Only installed models with compatible workflows are listed.')
        self.update_lora()

    def update_lora(self, *_):
        if not self.state:return
        combo = self.selectors['studio'];name = combo.currentData()
        current = self.state['current'].get(FIELDS['studio'][0])
        import json
        workflow = json.loads(self.state['catalog']['studio'].get(name, {}).get('config', {}).get(FIELDS['studio'][1], '{}'))
        has_lora = any(n.get('class_type') in ('LoraLoader', 'LoraLoaderModelOnly') for n in workflow.values())
        ready = name == current and has_lora and not self.busy
        self.lora_button.setEnabled(ready)
        self.lora_hint.setText('Choose LoRA files, enable them, and adjust their strength for Image Studio.' if ready else ('Save your model selection before opening LoRA settings.' if name != current else 'This model’s workflow has no LoRA controls. Your Z-Image LoRA choices are retained when you switch models.'))

    def open_lora(self):
        if self.lora_dialog is not None:self.lora_dialog.raise_();return
        self.lora_dialog = LoraDialog(self)
        self.save.setEnabled(False)
        for combo in self.selectors.values():combo.setEnabled(False)
        self.lora_dialog.destroyed.connect(self.lora_closed)
        self.lora_dialog.show()

    def lora_closed(self):
        self.lora_dialog = None;self.busy = True;self.save.setEnabled(False)
        self.work(lambda: request_settings(), 'load')

    def save_settings(self):
        self.busy = True;self.saving = True;self.save.setEnabled(False);self.lora_button.setEnabled(False)
        for combo in self.selectors.values():combo.setEnabled(False)
        original = self.state['current'];selected = {slot: combo.currentData() for slot, combo in self.selectors.items()}
        self.message.setText('Saving image settings…')
        self.work(lambda: request_settings('save', original=original, selected=selected), 'save')

    def closeEvent(self, event):
        if self.saving or self.lora_dialog is not None:event.ignore()
        else:super().closeEvent(event)

    def reject(self):
        if not self.saving and self.lora_dialog is None:
            super().reject()
