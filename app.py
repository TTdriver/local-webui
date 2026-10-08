#!/usr/bin/env python3
"""Dedicated local WebUI window with isolated storage and live system metrics."""
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
import psutil

# The VM display cannot reliably initialize Vulkan/GBM. Chromium still uses
# its compositor for scrolling with a supported software rendering backend.
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu --disable-features=Vulkan')
from PySide6.QtCore import QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox, QMenu
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEnginePermission
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from image_dialog import ImageDialog

from update_check import UpdateCheck

APP_VERSION = '0.1.1'
UPDATE_VERSION_URL = 'https://api.github.com/repos/TTdriver/local-webui/contents/VERSION'
DOWNLOAD_URL = 'https://github.com/TTdriver/local-webui#installation'

URL = 'http://127.0.0.1:3000'
PROFILE = Path.home() / '.local/share/local-webui'


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        if not main_frame:
            return True
        if (url.scheme() == 'http' and url.host() == '127.0.0.1' and url.port() == 3000) or url.scheme() in ('about', 'blob'):
            return True
        if url.scheme() in ('http', 'https', 'mailto'):
            window = self.parent().window() if isinstance(self.parent(), QWebEngineView) else None
            answer = QMessageBox.question(window, 'External link', 'Open this link in your browser?\n\n' + url.toString())
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(url)
        return False


class Window(QMainWindow):
    metrics_ready = Signal(dict)

    def __init__(self):
        super().__init__()
        os.umask(0o077)
        for directory in [PROFILE, PROFILE / 'qt-data', PROFILE / 'qt-cache']:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self.setWindowTitle('Local WebUI')
        self.setWindowIcon(QIcon(str(Path(__file__).with_name('local-webui.svg'))))
        self.resize(1120, 820)
        self.stop_metrics = threading.Event()
        self.metrics_ready.connect(self.update_metrics)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = QWidget()
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(12, 8, 12, 8)
        self.web = QWebEngineView()
        self.profile = QWebEngineProfile('LocalWebUI', self)
        self.profile.setPersistentStoragePath(str(PROFILE / 'qt-data'))
        self.profile.setCachePath(str(PROFILE / 'qt-cache'))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
        self.page = Page(self.profile, self.web)
        self.web.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        self.page.permissionRequested.connect(self.permission)
        self.page.newWindowRequested.connect(lambda request: request.openIn(self.page))
        self.profile.downloadRequested.connect(self.download)
        for label, tooltip, callback in [('‹', 'Back', self.web.back), ('⌂', 'Home', lambda: self.web.setUrl(QUrl(URL)))]:
            button = QPushButton(label)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setFixedWidth(36)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.stats = {}
        for name in ('CPU', 'RAM', 'GPU'):
            label = QLabel(name + ' —')
            row.addWidget(label)
            self.stats[name] = label
        row.addStretch()
        image_menu = QMenu(self)
        image_settings = image_menu.addAction('Image Settings…')
        image_settings.triggered.connect(self.open_image_settings)
        image_menu.addSeparator()
        delete_chats = image_menu.addAction('Delete all chats…')
        delete_chats.triggered.connect(self.confirm_delete_all_chats)
        image_button = QPushButton('Images ▾')
        image_button.setToolTip('Image settings and chat cleanup')
        image_button.setMenu(image_menu)
        row.addWidget(image_button)
        refresh = QPushButton('↻')
        refresh.setToolTip('Refresh (Ctrl+R / F5)')
        refresh.setAccessibleName('Refresh WebUI')
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self.web.reload)
        row.addWidget(refresh)
        layout.addWidget(toolbar)
        self.error_bar = QWidget()
        error_layout = QHBoxLayout(self.error_bar)
        error_layout.addWidget(QLabel('Could not load WebUI. Check that the server is running.'))
        retry = QPushButton('Retry')
        retry.clicked.connect(lambda: self.web.setUrl(QUrl(URL)))
        error_layout.addWidget(retry)
        layout.addWidget(self.error_bar)
        self.error_bar.hide()
        layout.addWidget(self.web, 1)
        self.update_footer = QWidget()
        update_row = QHBoxLayout(self.update_footer)
        update_row.setContentsMargins(12, 3, 12, 5)
        update_row.addStretch()
        self.update_link = QLabel('')
        self.update_link.setStyleSheet('color:#8e98a5; font-size:11px; padding:0;')
        self.update_link.setTextFormat(Qt.TextFormat.PlainText)
        self.update_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_link.mousePressEvent = lambda event: QDesktopServices.openUrl(QUrl(DOWNLOAD_URL))
        update_row.addWidget(self.update_link)
        self.update_footer.hide()
        layout.addWidget(self.update_footer)
        self.update_check = UpdateCheck(APP_VERSION, UPDATE_VERSION_URL, 'LocalWebUI')
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.poll_update)
        self.update_timer.start(100)
        self.update_check.start()
        self.setCentralWidget(root)
        self.web.loadStarted.connect(self.error_bar.hide)
        self.web.loadFinished.connect(lambda success: self.error_bar.setVisible(not success))
        self.page.renderProcessTerminated.connect(lambda *_: self.error_bar.show())
        for key in ('Ctrl+R', 'F5'):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(self.web.reload)
        self.setStyleSheet('QMainWindow, QWidget { background:#20242b; color:#edf2fa; } QLabel { padding:0 6px; } QPushButton { background:#303740; border:0; border-radius:5px; padding:6px; font-size:17px; } QPushButton:hover { background:#46515e; }')
        self.web.setUrl(QUrl(URL))
        threading.Thread(target=self.poll_metrics, daemon=True).start()

    def poll_update(self):
        try:
            version = self.update_check.results.get_nowait()
        except queue.Empty:
            return
        self.update_timer.stop()
        if version:
            self.update_link.setText(f'Update available · v{version} ↗')
            self.update_footer.show()

    def open_image_settings(self):
        if getattr(self, 'image_dialog', None) is not None:
            self.image_dialog.raise_()
            self.image_dialog.activateWindow()
            return
        self.image_dialog = ImageDialog(self)
        self.image_dialog.destroyed.connect(lambda: setattr(self, 'image_dialog', None))
        self.image_dialog.show()

    def confirm_delete_all_chats(self):
        answer = QMessageBox.question(
            self,
            'Delete all chats?',
            'Permanently delete every chat, including archived chats? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.page.url().host() != '127.0.0.1' or self.page.url().port() != 3000:
            QMessageBox.warning(self, 'Open WebUI is not ready', 'Open WebUI and sign in before deleting chats.')
            return
        script = "(async()=>{try{const r=await fetch('/api/v1/chats/',{method:'DELETE',credentials:'include'});return JSON.stringify({ok:r.ok,status:r.status});}catch(e){return JSON.stringify({ok:false,status:0});}})()"
        self.page.runJavaScript(script, self.on_delete_all_chats)

    def on_delete_all_chats(self, result):
        try:
            import json
            data = json.loads(result or '{}')
        except (TypeError, ValueError):
            data = {}
        if data.get('ok'):
            QMessageBox.information(self, 'Chats deleted', 'All chats, including archived chats, were deleted.')
            self.web.reload()
        else:
            detail = 'Sign in again, then retry.' if data.get('status') == 401 else 'Open WebUI could not delete the chats.'
            QMessageBox.warning(self, 'Could not delete chats', detail)

    def poll_metrics(self):
        while not self.stop_metrics.is_set():
            cpu = psutil.cpu_percent(interval=0.2)
            ram = psutil.virtual_memory()
            values = {'CPU': f'CPU {cpu:.0f}%',
                      'RAM': f'RAM {ram.percent:.0f}% · {(ram.total-ram.available)/1024**3:.1f}/{ram.total/1024**3:.1f} GB',
                      'GPU': 'GPU unavailable'}
            try:
                result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2, check=True)
                usage, used, total = map(float, result.stdout.splitlines()[0].split(','))
                values['GPU'] = f'GPU {usage:.0f}% · VRAM {used/1024:.1f}/{total/1024:.1f} GB'
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass
            if not self.stop_metrics.is_set():
                self.metrics_ready.emit(values)
            self.stop_metrics.wait(2)

    def update_metrics(self, values):
        for name, value in values.items():
            if self.stats[name].text() != value:
                self.stats[name].setText(value)

    def permission(self, permission):
        allowed = (QWebEnginePermission.PermissionType.MediaAudioCapture,
                   QWebEnginePermission.PermissionType.MediaVideoCapture,
                   QWebEnginePermission.PermissionType.MediaAudioVideoCapture)
        if permission.permissionType() in allowed:
            answer = QMessageBox.question(self, 'WebUI permission', 'Allow WebUI to use your microphone or camera?')
            permission.grant() if answer == QMessageBox.Yes else permission.deny()
        else:
            permission.deny()

    def download(self, request):
        path, _ = QFileDialog.getSaveFileName(self, 'Save WebUI download', str(Path.home() / 'Downloads' / Path(request.downloadFileName()).name))
        if path:
            destination = Path(path)
            request.setDownloadDirectory(str(destination.parent))
            request.setDownloadFileName(destination.name)
            request.accept()
        else:
            request.cancel()

    def closeEvent(self, event):
        self.stop_metrics.set()
        super().closeEvent(event)


def main():
    application = QApplication(sys.argv)
    application.setApplicationName('local-webui')
    application.setDesktopFileName('local-webui')
    application.setOrganizationName('LocalWebUI')
    name = 'local-webui-' + str(os.getuid())
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(250):
        socket.write(b'activate')
        socket.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(name)
    if not server.listen(name):
        raise RuntimeError('Could not start Local WebUI')
    window = Window()
    def activate():
        client = server.nextPendingConnection()
        client.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()
    server.newConnection.connect(activate)
    window.show()
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
