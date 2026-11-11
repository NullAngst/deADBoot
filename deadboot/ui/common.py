"""Shared UI pieces: the app context, the tab base class and the confirmation dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..ops import DeviceOps
from ..runner import Result


class App:
    """Everything a tab needs: runner, tools, settings, jobs, the device model and the window."""

    def __init__(self, settings, tools, runner, jobs, profiles, protected):
        self.settings = settings
        self.tools = tools
        self.runner = runner
        self.jobs = jobs
        self.profiles = profiles
        self.protected = protected
        self.devices = None        # DeviceModel, set by the window
        self.window = None
        self.current = None        # DeviceInfo

    def ops(self, worker=None, planning: bool = False) -> DeviceOps:
        serial = self.current.serial if self.current else ""
        return DeviceOps(self.runner, serial, planning=planning, cancel=worker.cancel if worker else None,
                         mode_hint=self.current.state if self.current else "device")


def open_url(url: str) -> None:
    if url:
        QDesktopServices.openUrl(QUrl(url))


def open_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def ask(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


def pick_file(parent, title: str, filt: str = "All files (*)") -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, str(Path.home()), filt)
    return path


def pick_files(parent, title: str, filt: str = "All files (*)") -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, title, str(Path.home()), filt)
    return paths


def pick_dir(parent, title: str) -> str:
    return QFileDialog.getExistingDirectory(parent, title, str(Path.home()))


def device_line(dev) -> str:
    return f"{dev.confirm_name}  (serial {dev.serial})"


def confirm_device(parent, dev, action: str) -> bool:
    """Plain confirmation that names the device, for actions that write but are not destructive."""
    return ask(parent, "Confirm", f"{action}\n\nDevice: {device_line(dev)}")


class TypedConfirm(QDialog):
    """Destructive actions need the device's model name typed in, not a click."""

    def __init__(self, parent, dev, action: str, details: str = "", danger_lines: list[str] | None = None,
                 expected: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Confirm destructive action")
        self.expected = (expected or dev.confirm_name).strip()
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{action}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(QLabel(f"Device: {device_line(dev)}"))
        for line in danger_lines or []:
            lab = QLabel(line)
            lab.setWordWrap(True)
            lab.setProperty("danger", True)
            lay.addWidget(lab)
        if details:
            box = QPlainTextEdit(details)
            box.setReadOnly(True)
            box.setMinimumHeight(140)
            lay.addWidget(box)
        lay.addWidget(QLabel(f"Type <b>{self.expected}</b> to continue:"))
        self.edit = QLineEdit()
        lay.addWidget(self.edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText("Do it")
        self.ok_btn.setProperty("danger", True)
        self.ok_btn.setEnabled(False)
        self.edit.textChanged.connect(lambda t: self.ok_btn.setEnabled(t.strip() == self.expected))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.resize(560, 300)


def typed_confirm(parent, dev, action: str, details: str = "", danger_lines: list[str] | None = None,
                  expected: str | None = None) -> bool:
    if dev is None:
        return False
    return TypedConfirm(parent, dev, action, details, danger_lines, expected).exec() == QDialog.DialogCode.Accepted


class TextDialog(QDialog):
    def __init__(self, parent, title: str, text: str, copy: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        self.box = QPlainTextEdit(text)
        self.box.setReadOnly(True)
        lay.addWidget(self.box)
        row = QHBoxLayout()
        if copy:
            b = QPushButton("&Copy")
            b.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.box.toPlainText()))
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("C&lose")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(760, 520)


def show_text(parent, title: str, text: str) -> None:
    TextDialog(parent, title, text).exec()


class Tab(QWidget):
    """Base class: knows the current device and greys out what its mode can't do."""

    title = "Tab"

    def __init__(self, app: App, parent=None):
        super().__init__(parent)
        self.app = app
        self._gated: list[tuple[QWidget, set[str] | None]] = []

    @property
    def dev(self):
        return self.app.current

    def gate(self, widget: QWidget, modes: set[str] | None = None) -> QWidget:
        """Enable widget only when a device in one of these modes is selected (None: any device)."""
        self._gated.append((widget, modes))
        return widget

    def device_changed(self, dev) -> None:
        for w, modes in self._gated:
            ok = dev is not None and (modes is None or dev.state in modes)
            w.setEnabled(ok)
        self.on_device(dev)

    def on_device(self, dev) -> None:
        pass

    def job(self, fn: Callable, done: Callable | None = None, progress: Callable | None = None):
        return self.app.jobs.run(fn, done, self.job_failed, progress)

    def job_failed(self, err: str) -> None:
        self.app.runner.line.emit("! " + err.splitlines()[0])
        warn(self, "Error", err)

    def result_box(self, r: Result, ok_text: str = "Done.") -> None:
        if r.dry:
            info(self, "Dry run", "Dry-run mode: the command was printed in the log, not run.")
        elif r.ok:
            info(self, "Done", ok_text)
        else:
            warn(self, "Failed", (r.err or r.out or f"exit code {r.code}").strip()[-2000:])
