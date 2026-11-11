"""Diagnostics tab: live logcat, getprop, fastboot getvar all, battery and storage, bug report."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QTabWidget, QVBoxLayout, QWidget)

from ... import parsers
from ...runner import Command
from ..common import Tab, info, warn

LEVELS = ["V", "D", "I", "W", "E", "F"]


class DiagnosticsTab(Tab):
    title = "Diagnostics"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        lay = QVBoxLayout(self)
        self.sub = QTabWidget()
        lay.addWidget(self.sub)

        # logcat
        lw = QWidget()
        ll = QVBoxLayout(lw)
        row = QHBoxLayout()
        self.level = QComboBox()
        self.level.addItems(LEVELS)
        self.level.setCurrentText("I")
        self.tag = QLineEdit()
        self.tag.setPlaceholderText("Tag (optional)")
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("Package (optional)")
        self.start_btn = self.gate(QPushButton("&Start"), {"device", "recovery"})
        self.pause_btn = QPushButton("&Pause")
        self.pause_btn.setCheckable(True)
        self.stop_btn = QPushButton("S&top")
        self.clear_btn = QPushButton("&Clear")
        self.save_btn = QPushButton("Sa&ve...")
        row.addWidget(QLabel("Level"))
        for w in (self.level, self.tag, self.pkg, self.start_btn, self.pause_btn, self.stop_btn, self.clear_btn,
                  self.save_btn):
            row.addWidget(w)
        ll.addLayout(row)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(20000)
        self.logview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        ll.addWidget(self.logview)
        self.sub.addTab(lw, "logcat")

        # getprop
        gw = QWidget()
        gl = QVBoxLayout(gw)
        r2 = QHBoxLayout()
        self.prop_search = QLineEdit()
        self.prop_search.setPlaceholderText("Search properties")
        self.prop_btn = self.gate(QPushButton("&Read getprop"), {"device", "recovery"})
        r2.addWidget(self.prop_search, 1)
        r2.addWidget(self.prop_btn)
        gl.addLayout(r2)
        self.props_view = QPlainTextEdit()
        self.props_view.setReadOnly(True)
        gl.addWidget(self.props_view)
        self.sub.addTab(gw, "getprop")

        # getvar
        vw = QWidget()
        vl = QVBoxLayout(vw)
        self.var_btn = self.gate(QPushButton("Read fastboot &getvar all"), {"bootloader", "fastbootd"})
        vl.addWidget(self.var_btn)
        self.vars_view = QPlainTextEdit()
        self.vars_view.setReadOnly(True)
        vl.addWidget(self.vars_view)
        self.sub.addTab(vw, "getvar")

        # battery and storage
        bw = QWidget()
        bl = QVBoxLayout(bw)
        r3 = QHBoxLayout()
        self.batt_btn = self.gate(QPushButton("Read &battery and storage"), {"device"})
        self.bug_btn = self.gate(QPushButton("Save bug report &bundle..."), {"device"})
        r3.addWidget(self.batt_btn)
        r3.addWidget(self.bug_btn)
        r3.addStretch(1)
        bl.addLayout(r3)
        self.batt_view = QPlainTextEdit()
        self.batt_view.setReadOnly(True)
        bl.addWidget(self.batt_view)
        self.sub.addTab(bw, "Battery, storage, bug report")

        self.proc = None
        self.paused_buffer: list[str] = []
        self.all_props: dict[str, str] = {}
        self.start_btn.clicked.connect(self.start_logcat)
        self.stop_btn.clicked.connect(self.stop_logcat)
        self.pause_btn.toggled.connect(self.toggle_pause)
        self.clear_btn.clicked.connect(self.logview.clear)
        self.save_btn.clicked.connect(self.save_log)
        self.prop_btn.clicked.connect(self.read_props)
        self.prop_search.textChanged.connect(self.render_props)
        self.var_btn.clicked.connect(self.read_vars)
        self.batt_btn.clicked.connect(self.read_battery)
        self.bug_btn.clicked.connect(self.bugreport)

    def on_device(self, dev) -> None:
        self.stop_logcat()

    # ------------------------------------------------------------ logcat

    def start_logcat(self) -> None:
        self.stop_logcat()
        serial = self.dev.serial
        level = self.level.currentText()
        tag = self.tag.text().strip()
        pkg = self.pkg.text().strip()

        def work(w):
            if not pkg:
                return ""
            return self.app.ops(w).qshell(f"pidof {pkg}").strip().split(" ")[0]

        def go(pid: str) -> None:
            if pkg and not pid:
                warn(self, "logcat", f"{pkg} is not running, so there is no process to filter on.")
                return
            args = ["logcat", "-v", "threadtime"]
            if pid:
                args.append(f"--pid={pid}")
            args += [f"{tag}:{level}", "*:S"] if tag else [f"*:{level}"]
            self.proc = self.app.runner.start(Command("adb", args, serial), on_line=self.add_line,
                                              on_finished=lambda _c: setattr(self, "proc", None))

        self.job(work, go)

    def add_line(self, line: str) -> None:
        if self.pause_btn.isChecked():
            if len(self.paused_buffer) < 20000:
                self.paused_buffer.append(line)
            return
        self.logview.appendPlainText(line)

    def toggle_pause(self, paused: bool) -> None:
        if not paused and self.paused_buffer:
            self.logview.appendPlainText("\n".join(self.paused_buffer))
            self.paused_buffer.clear()

    def stop_logcat(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc = None

    def save_log(self) -> None:
        name = f"logcat-{_dt.datetime.now():%Y%m%d-%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save logcat", str(Path.home() / name), "Text (*.txt)")
        if path:
            Path(path).write_text(self.logview.toPlainText(), encoding="utf-8")

    # ------------------------------------------------------------ props and vars

    def read_props(self) -> None:
        def done(props):
            self.all_props = props
            self.render_props()
        self.job(lambda w: self.app.ops(w).getprop(), done)

    def render_props(self) -> None:
        q = self.prop_search.text().strip().lower()
        lines = [f"[{k}]: [{v}]" for k, v in sorted(self.all_props.items()) if q in k.lower() or q in v.lower()]
        self.props_view.setPlainText("\n".join(lines))

    def read_vars(self) -> None:
        self.job(lambda w: self.app.ops(w).getvar_all(),
                 lambda v: self.vars_view.setPlainText("\n".join(f"{k}: {val}" for k, val in sorted(v.items()))))

    def read_battery(self) -> None:
        def work(w):
            ops = self.app.ops(w)
            batt = parsers.parse_dumpsys_battery(ops.qshell("dumpsys battery"))
            df = ops.qshell("df -h /data /sdcard /system 2>/dev/null")
            return batt, df

        def done(res):
            batt, df = res
            temp = batt.get("temperature", "")
            lines = [f"Level: {batt.get('level', '?')}%",
                     f"Status code: {batt.get('status', '?')} (2 charging, 3 discharging, 5 full)",
                     f"Health code: {batt.get('health', '?')} (2 good, 3 overheat, 4 dead, 7 cold)",
                     f"Temperature: {int(temp) / 10:.1f} C" if temp.lstrip('-').isdigit() else "Temperature: ?",
                     f"Voltage: {batt.get('voltage', '?')} mV",
                     f"Charge counter: {batt.get('Charge counter', batt.get('charge counter', '?'))}",
                     "", "Storage:", df]
            self.batt_view.setPlainText("\n".join(lines))

        self.job(work, done)

    def bugreport(self) -> None:
        name = f"bugreport-{self.dev.codename or self.dev.serial}-{_dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Save bug report", str(Path.home() / name), "Zip (*.zip)")
        if not path:
            return
        info(self, "Bug report", "This takes a few minutes. The log pane shows progress.")
        serial = self.dev.serial
        self.job(lambda w: self.app.runner.run_blocking(Command("adb", ["bugreport", path], serial), cancel=w.cancel),
                 lambda r: info(self, "Bug report", f"Saved {path}") if r.ok else warn(self, "Bug report", r.text))
