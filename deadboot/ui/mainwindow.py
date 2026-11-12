"""Main window: device list on the left, tabs in the middle, log pane at the bottom, top bar for scrcpy."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                             QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, hostplatform, parsers
from ..runner import Command
from . import themes
from .common import info, warn, wrap_long_labels
from .devicemodel import DeviceModel
from .dialogs import SettingsDialog, UsbHelpDialog, WirelessDialog, fetch_platform_tools, fetch_scrcpy
from .tabs.apps import AppsTab
from .tabs.backup import BackupTab
from .tabs.diagnostics import DiagnosticsTab
from .tabs.fdroid import FdroidTab
from .tabs.files import FilesTab, human
from .tabs.flash import FlashTab
from .tabs.recipes_tab import RecipesTab

GUIDANCE = {
    "unauthorized": "Look at the phone's screen: it is asking to allow USB debugging from this computer. Tap "
                    "Allow (tick Always allow). If no prompt shows, unplug and replug, or revoke USB debugging "
                    "authorizations in Developer options and try again. If the screen is dead, see Help, "
                    "Dead screen.",
    "no-permissions": "Your user can't open the USB device. That needs udev rules. Press USB help.",
    "offline": "adb sees the phone but can't talk to it. Replug it, try another cable or port (charge-only "
               "cables are common), or restart adb with Restart adb.",
    "recovery": "The phone is in recovery. Files work if the recovery exposes adb; sideload needs Apply from ADB.",
    "sideload": "The phone is waiting for adb sideload.",
    "bootloader": "The phone is in the bootloader (fastboot). Only the Flashing tab and getvar work here.",
    "fastbootd": "The phone is in fastbootd, the userspace fastboot for dynamic partitions.",
    "download": "Samsung download mode. Only heimdall in the Flashing tab works here.",
}

DEAD_SCREEN = (
    "adb needs the phone to approve this computer's RSA key, which means tapping a prompt on the phone's screen. "
    "If the screen is dead and debugging was never authorized for this computer, scrcpy can't help, because scrcpy "
    "itself needs adb. deADBoot can't solve this in software.\n\n"
    "Workarounds that sometimes work:\n"
    "- A USB-C or MHL video adapter to a monitor, plus a USB mouse through an OTG hub, to tap the prompt.\n"
    "- In a custom recovery that exposes adb, copy this computer's key onto the device:\n"
    "    adb push ~/.android/adbkey.pub /data/misc/adb/adb_keys\n"
    "  (Windows: %USERPROFILE%\\.android\\adbkey.pub). Then reboot. Data must be decrypted in recovery for "
    "this to stick.")


class ScrcpyDialog(QDialog):
    def __init__(self, parent, dev):
        super().__init__(parent)
        self.setWindowTitle(f"Mirror {dev.confirm_name}")
        form = QFormLayout(self)
        self.max_size = QSpinBox()
        self.max_size.setRange(0, 4096)
        self.max_size.setSpecialValueText("device native")
        self.max_size.setValue(1600)
        self.bitrate = QSpinBox()
        self.bitrate.setRange(1, 200)
        self.bitrate.setSuffix(" Mbps")
        self.bitrate.setValue(8)
        self.record = QCheckBox("Record to a file in your home folder")
        self.awake = QCheckBox("Stay awake while plugged in")
        self.awake.setChecked(True)
        self.screen_off = QCheckBox("Turn the phone's screen off while mirroring")
        self.audio = QCheckBox("Forward audio (Android 11+)")
        self.audio.setChecked(dev.sdk >= 30)
        self.audio.setEnabled(dev.sdk >= 30 or dev.sdk == 0)
        form.addRow("Max resolution", self.max_size)
        form.addRow("Bitrate cap", self.bitrate)
        for w in (self.record, self.awake, self.screen_off, self.audio):
            form.addRow("", w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Start")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def args(self, dev) -> list[str]:
        a = [f"--video-bit-rate={self.bitrate.value()}M", f"--window-title={dev.confirm_name} ({dev.serial})"]
        if self.max_size.value():
            a.append(f"--max-size={self.max_size.value()}")
        if self.record.isChecked():
            from datetime import datetime
            from pathlib import Path
            a.append(f"--record={Path.home() / f'scrcpy-{dev.codename or dev.serial}-{datetime.now():%Y%m%d-%H%M%S}.mkv'}")
        if self.awake.isChecked():
            a.append("--stay-awake")
        if self.screen_off.isChecked():
            a.append("--turn-screen-off")
        if not self.audio.isChecked():
            a.append("--no-audio")
        return a


class MainWindow(QMainWindow):
    def __init__(self, app_ctx):
        super().__init__()
        self.app = app_ctx
        app_ctx.window = self
        self.last_battery: dict[str, int] = {}
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 880)
        self.setAcceptDrops(True)

        # ------------------------------------------------ top bar
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.scrcpy_btn = QPushButton("&Mirror screen (scrcpy)")
        self.scrcpy_btn.clicked.connect(self.launch_scrcpy)
        bar.addWidget(self.scrcpy_btn)
        self.wireless_btn = QPushButton("&Wireless debugging...")
        self.wireless_btn.clicked.connect(lambda: WirelessDialog(self, self.app).exec())
        bar.addWidget(self.wireless_btn)
        self.usb_btn = QPushButton("USB &help...")
        self.usb_btn.clicked.connect(lambda: self.usb_help())
        bar.addWidget(self.usb_btn)
        restart = QPushButton("Restart a&db")
        restart.clicked.connect(self.restart_adb)
        bar.addWidget(restart)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        bar.addWidget(spacer)
        self.dry_label = QLabel("DRY RUN")
        self.dry_label.setObjectName("dryrun")
        self.dry_label.setVisible(False)
        bar.addWidget(self.dry_label)
        self.tool_status = QLabel("")
        self.tool_status.setObjectName("toolstatus")
        bar.addWidget(self.tool_status)
        settings = QPushButton("&Settings")
        settings.clicked.connect(self.open_settings)
        bar.addWidget(settings)
        helpb = QPushButton("He&lp")
        helpb.clicked.connect(self.show_help)
        bar.addWidget(helpb)

        # ------------------------------------------------ device panel
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Devices</b>"))
        self.dev_list = QListWidget()
        self.dev_list.setObjectName("devicelist")
        self.dev_list.currentItemChanged.connect(self.select_device)
        ll.addWidget(self.dev_list, 1)
        self.dev_info = QLabel("No device.")
        self.dev_info.setWordWrap(True)
        self.dev_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ll.addWidget(self.dev_info)
        self.guidance = QLabel("")
        self.guidance.setWordWrap(True)
        ll.addWidget(self.guidance)
        self.fix_btn = QPushButton("Fix &this")
        self.fix_btn.setVisible(False)
        self.fix_btn.clicked.connect(self.fix_state)
        ll.addWidget(self.fix_btn)
        refresh = QPushButton("&Refresh devices")
        refresh.clicked.connect(lambda: self.app.devices.poll())
        ll.addWidget(refresh)
        left.setMinimumWidth(260)

        # ------------------------------------------------ tabs
        self.tabs = QTabWidget()
        self.tab_list = [FilesTab(self.app), AppsTab(self.app), FdroidTab(self.app), BackupTab(self.app),
                         FlashTab(self.app), RecipesTab(self.app), DiagnosticsTab(self.app)]
        self.tabs_by_name = {}
        for i, t in enumerate(self.tab_list):
            label = t.title.replace("&", "&&") + ("  (danger)" if isinstance(t, FlashTab) else "")
            self.tabs.addTab(t, f"&{i + 1} {label}" if i < 9 else label)
            self.tabs_by_name[t.title] = t
            act = QAction(self)
            act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(lambda _c=False, w=t: self.tabs.setCurrentWidget(w))
            self.addAction(act)

        # ------------------------------------------------ log pane
        logw = QWidget()
        lgl = QVBoxLayout(logw)
        lgl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.log_toggle = QPushButton("&Log")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(True)
        copy = QPushButton("Cop&y log")
        clear = QPushButton("Clea&r log")
        row.addWidget(self.log_toggle)
        row.addWidget(copy)
        row.addWidget(clear)
        row.addStretch(1)
        lgl.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setObjectName("logpane")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(10000)
        lgl.addWidget(self.log)
        self.log_toggle.toggled.connect(self.log.setVisible)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log.toPlainText()))
        clear.clicked.connect(self.log.clear)
        self.app.runner.line.connect(self.log.appendPlainText)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.tabs)
        vsplit.addWidget(logw)
        vsplit.setSizes([620, 220])
        hsplit = QSplitter()
        hsplit.addWidget(left)
        hsplit.addWidget(vsplit)
        hsplit.setSizes([290, 1070])
        self.setCentralWidget(hsplit)

        # ------------------------------------------------ devices
        self.app.devices = DeviceModel(self.app, self)
        self.app.devices.changed.connect(self.devices_changed)
        self.app.devices.device_updated.connect(self.device_updated)
        self.app.devices.windows_driver_hint.connect(self.windows_hint)
        self.update_tools()
        for t in self.tab_list:
            t.device_changed(None)
        wrap_long_labels(self)
        self.fit_to_screen()

    def fit_to_screen(self) -> None:
        """Open at 1360x880, or smaller when the screen is smaller, centered on the screen under the cursor.

        The window used to size itself from its widest widget, which on a multi-monitor desktop could
        stretch it across all of them.
        """
        from PyQt6.QtGui import QCursor
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = min(1360, int(avail.width() * 0.92))
        h = min(880, int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    # ================================================================ devices

    def devices_changed(self, devices) -> None:
        cur = self.app.current.serial if self.app.current else None
        self.dev_list.blockSignals(True)
        self.dev_list.clear()
        select = None
        for d in devices:
            it = QListWidgetItem(d.label + f"\n{d.serial}")
            it.setData(Qt.ItemDataRole.UserRole, d.serial)
            self.dev_list.addItem(it)
            if d.serial == cur:
                select = it
        self.dev_list.blockSignals(False)
        if select is None and self.dev_list.count():
            select = self.dev_list.item(0)
        if select is not None:
            self.dev_list.setCurrentItem(select)
            self.select_device(select, None)
        else:
            self.select_device(None, None)

    def device_updated(self, d) -> None:
        if d.battery is not None:
            self.last_battery[d.serial] = d.battery
        for i in range(self.dev_list.count()):
            it = self.dev_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == d.serial:
                it.setText(d.label + f"\n{d.serial}")
        if self.app.current and self.app.current.serial == d.serial:
            self.render_info(d)
            for t in self.tab_list:
                if isinstance(t, FlashTab):
                    t.on_device(d)

    def select_device(self, item, _prev) -> None:
        serial = item.data(Qt.ItemDataRole.UserRole) if item else None
        dev = self.app.devices.devices.get(serial) if serial else None
        changed = (self.app.current.serial if self.app.current else None) != serial or \
            (dev and self.app.current and dev.state != getattr(self, "_last_state", None))
        self.app.current = dev
        self._last_state = dev.state if dev else None
        self.render_info(dev)
        if changed or dev is None:
            for t in self.tab_list:
                t.device_changed(dev)
        self.scrcpy_btn.setEnabled(bool(dev and dev.adb_ready and self.app.tools.available("scrcpy")))

    def render_info(self, d) -> None:
        if d is None:
            self.dev_info.setText("No device. Plug one in with USB debugging on, or use Wireless debugging.")
            self.guidance.setText("")
            self.fix_btn.setVisible(False)
            return
        rows = [f"<b>{d.confirm_name}</b>", f"Codename: {d.codename or '?'}",
                f"Android: {d.android or '?'}" + (f" (SDK {d.sdk})" if d.sdk else ""), f"Serial: {d.serial}",
                f"Mode: {d.state}"]
        if d.battery is not None:
            rows.append(f"Battery: {d.battery}%")
        if d.storage_free_kb is not None:
            rows.append(f"Free storage: {human(d.storage_free_kb * 1024)}")
        rows.append(f"Bootloader: {d.lock}")
        if d.slot:
            rows.append(f"Slot: {d.slot}")
        rows.append(f"Root: {'detected' if d.root else 'no'}")
        self.dev_info.setText("<br>".join(rows))
        self.guidance.setText(GUIDANCE.get(d.state, ""))
        self.fix_btn.setVisible(d.state in ("no-permissions", "offline"))
        self.fix_btn.setText("USB &help..." if d.state == "no-permissions" else "Restart a&db")

    def fix_state(self) -> None:
        d = self.app.current
        if d and d.state == "no-permissions":
            self.usb_help()
        else:
            self.restart_adb()

    def usb_help(self, manufacturer: str = "") -> None:
        d = self.app.current
        UsbHelpDialog(self, self.app, manufacturer or (d.manufacturer if d else ""),
                      bool(d and d.in_fastboot)).exec()

    def windows_hint(self, manufacturer: str) -> None:
        self.guidance.setText(f"Windows sees a {manufacturer} device on USB but adb and fastboot don't. That is a "
                              "driver problem. Press USB help.")

    def restart_adb(self) -> None:
        def work(_w):
            self.app.runner.run_blocking(Command("adb", ["kill-server"]))
            return self.app.runner.run_blocking(Command("adb", ["start-server"]))
        self.app.jobs.run(work, lambda _r: self.app.devices.poll())

    # ================================================================ tools

    def update_tools(self) -> None:
        t = self.app.tools
        t.refresh()
        missing = [n for n in ("adb", "fastboot") if not t.available(n)]
        if missing:
            self.tool_status.setText("adb/fastboot missing")
            self.tool_status.setProperty("state", "warn")
        else:
            self.tool_status.setText("adb/fastboot found")
            self.tool_status.setProperty("state", "ok")
        self.tool_status.style().unpolish(self.tool_status)
        self.tool_status.style().polish(self.tool_status)
        if not t.available("scrcpy"):
            self.scrcpy_btn.setEnabled(False)
            self.scrcpy_btn.setToolTip("scrcpy not found: install " + t.hint("scrcpy"))
        else:
            self.scrcpy_btn.setToolTip("")

        def work(_w):
            return t.detect_versions(self.app.runner)

        def done(_st):
            fb = t.status("fastboot")
            from ..profiles import max_min_fastboot
            need = max_min_fastboot(self.app.profiles)
            if fb.found and need and not parsers.version_at_least(fb.version, need):
                self.tool_status.setText(f"fastboot {parsers.version_str(fb.version)} is old")
                self.tool_status.setProperty("state", "warn")
                self.tool_status.setToolTip(f"Some ROM profiles need fastboot {need}+. Distro packages lag. Use "
                                            "Settings, Fetch / update platform-tools.")
                self.tool_status.style().unpolish(self.tool_status)
                self.tool_status.style().polish(self.tool_status)
            elif fb.found:
                self.tool_status.setText(f"adb/fastboot {parsers.version_str(fb.version)}")

        if not missing:
            self.app.jobs.run(work, done)

    def first_run_tools(self) -> None:
        if not self.app.tools.available("adb") or not self.app.tools.available("fastboot"):
            fetch_platform_tools(self, self.app, self.after_tools_change)
        elif hostplatform.IS_WINDOWS and not self.app.tools.available("scrcpy"):
            self.log.appendPlainText("# scrcpy not found. Settings has a button to fetch the Windows release.")

    def after_tools_change(self) -> None:
        self.update_tools()
        self.app.devices.start()

    def update_dry_indicator(self) -> None:
        self.dry_label.setVisible(self.app.runner.dry_run)

    # ================================================================ actions

    def launch_scrcpy(self) -> None:
        d = self.app.current
        if not d:
            return
        if not self.app.tools.available("scrcpy"):
            if hostplatform.IS_WINDOWS:
                fetch_scrcpy(self, self.app, self.update_tools)
            else:
                warn(self, "scrcpy", "scrcpy not found: install " + self.app.tools.hint("scrcpy"))
            return
        dlg = ScrcpyDialog(self, d)
        if dlg.exec():
            self.app.runner.start(Command("scrcpy", dlg.args(d), d.serial), quiet=False)

    def open_settings(self) -> None:
        old_theme = self.app.settings.theme
        if SettingsDialog(self, self.app).exec():
            if self.app.settings.theme != old_theme:
                from PyQt6.QtWidgets import QApplication
                themes.apply(QApplication.instance(), self.app.settings.theme)
            self.after_tools_change()

    def show_help(self) -> None:
        from .common import show_text
        show_text(self, "Help", HELP_TEXT + "\n\nDead screen\n" + DEAD_SCREEN)

    def show_tab(self, tab) -> None:
        self.tabs.setCurrentWidget(tab)

    # ================================================================ drag and drop

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        cur = self.tabs.currentWidget()
        if hasattr(cur, "drop_files"):
            cur.drop_files(paths)
        elif all(p.lower().endswith((".apk", ".apks", ".xapk")) for p in paths):
            self.tabs_by_name["Apps"].drop_files(paths)
            self.show_tab(self.tabs_by_name["Apps"])
        else:
            self.tabs_by_name["Files"].drop_files(paths)
            info(self, "Push", "Dropped files are queued to push into the Files tab's current folder.")

    def closeEvent(self, e):
        self.app.devices.stop()
        self.app.runner.stop_all()
        self.app.jobs.stop_all()
        super().closeEvent(e)


HELP_TEXT = """deADBoot wraps adb, fastboot, heimdall and scrcpy. Every command it runs is shown in the log pane and
written to the action log in your data folder.

Keyboard: Ctrl+1 to Ctrl+7 switch tabs. Alt plus the underlined letter presses a button.

Dry run: tick "Show me the commands" in the Flashing tab. Commands that change a device are printed, not run.
Read-only commands still run, so the device state stays accurate.

Destructive actions ask you to type the device's model name. Recipes ask once, after showing the whole plan.

Developer verification: Google's sideloading rules exempt adb installs, as of this build. Google has not
promised that forever. If it changes, the install features are the ones affected."""
