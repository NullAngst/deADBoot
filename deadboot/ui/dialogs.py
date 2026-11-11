"""Settings, USB help, wireless debugging and tool-fetch dialogs."""

from __future__ import annotations

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from .. import downloads, hostplatform, parsers, usbhelp
from ..runner import Command
from .common import info, open_url, pick_dir, pick_file, warn
from .themes import THEMES


class SettingsDialog(QDialog):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        s = app.settings
        self.setWindowTitle("Settings")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.theme = QComboBox()
        for key, (label, _f) in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        form.addRow("&Theme", self.theme)

        self.pt_dir = QLineEdit(s.platform_tools_dir)
        self.pt_dir.setPlaceholderText("Empty: PATH first, then the fetched copy")
        form.addRow("&Platform-tools folder", self._browse(self.pt_dir, folder=True))
        self.force = QCheckBox("Use the fetched platform-tools even when a distro copy exists")
        self.force.setChecked(s.force_fetched_tools)
        form.addRow("", self.force)
        self.scrcpy = QLineEdit(s.scrcpy_path)
        form.addRow("&scrcpy", self._browse(self.scrcpy))
        self.heimdall = QLineEdit(s.heimdall_path)
        form.addRow("&heimdall", self._browse(self.heimdall))
        self.dumper = QLineEdit(s.payload_dumper_path)
        form.addRow("Payload &dumper", self._browse(self.dumper))
        self.abe = QLineEdit(s.abe_path)
        self.abe.setPlaceholderText("abe binary or abe.jar (runs with java)")
        form.addRow("&abe", self._browse(self.abe))
        self.cache = QLineEdit(s.cache_dir)
        self.cache.setPlaceholderText(str(hostplatform.cache_dir()))
        form.addRow("Download &cache", self._browse(self.cache, folder=True))
        self.batt = QSpinBox()
        self.batt.setRange(5, 100)
        self.batt.setSuffix(" %")
        self.batt.setValue(s.battery_threshold)
        form.addRow("&Battery threshold for flashing", self.batt)
        self.ret = QSpinBox()
        self.ret.setRange(1, 3650)
        self.ret.setSuffix(" days")
        self.ret.setValue(s.log_retention_days)
        form.addRow("&Log retention", self.ret)
        self.poll = QSpinBox()
        self.poll.setRange(1, 30)
        self.poll.setSuffix(" s")
        self.poll.setValue(s.poll_seconds)
        form.addRow("Device &refresh interval", self.poll)
        lay.addLayout(form)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(150)
        lay.addWidget(QLabel("Tools found"))
        lay.addWidget(self.status)
        row = QHBoxLayout()
        fetch = QPushButton("&Fetch / update platform-tools")
        fetch.clicked.connect(lambda: fetch_platform_tools(self, app, self.refresh_status))
        row.addWidget(fetch)
        if hostplatform.IS_WINDOWS:
            sc = QPushButton("Fetch s&crcpy")
            sc.clicked.connect(lambda: fetch_scrcpy(self, app, self.refresh_status))
            row.addWidget(sc)
        check = QPushButton("Check for tool &updates")
        check.clicked.connect(lambda: check_tool_updates(self, app))
        row.addWidget(check)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.refresh_status()
        self.resize(720, 640)

    def _browse(self, edit: QLineEdit, folder: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        b = QPushButton("...")
        b.clicked.connect(lambda: edit.setText((pick_dir(self, "Folder") if folder else
                                                pick_file(self, "Program")) or edit.text()))
        row.addWidget(b)
        return row

    def refresh_status(self) -> None:
        self.app.tools.refresh()

        def work(_w):
            return self.app.tools.detect_versions(self.app.runner)

        def done(st):
            names = ["adb", "fastboot", "scrcpy", "heimdall", "payload-dumper", "ssh-keygen", "gpg", "minisign",
                     "abe", "lz4"]
            lines = []
            for n in names:
                t = self.app.tools.status(n)
                if t.found:
                    v = f" {parsers.version_str(t.version)}" if t.version else ""
                    lines.append(f"{n}{v}: {t.path} ({t.source})")
                else:
                    lines.append(f"{n}: not found. Install {self.app.tools.hint(n)}")
            self.status.setPlainText("\n".join(lines))

        self.app.jobs.run(work, done)

    def save(self) -> None:
        s = self.app.settings
        s.theme = self.theme.currentData()
        s.platform_tools_dir = self.pt_dir.text().strip()
        s.force_fetched_tools = self.force.isChecked()
        s.scrcpy_path = self.scrcpy.text().strip()
        s.heimdall_path = self.heimdall.text().strip()
        s.payload_dumper_path = self.dumper.text().strip()
        s.abe_path = self.abe.text().strip()
        s.cache_dir = self.cache.text().strip()
        s.battery_threshold = self.batt.value()
        s.log_retention_days = self.ret.value()
        s.poll_seconds = self.poll.value()
        s.save()
        self.app.tools.refresh()
        self.accept()


def fetch_platform_tools(parent, app, after=None) -> None:
    if not ask_fetch(parent, "Google's platform-tools (adb and fastboot)", hostplatform.platform_tools_url(),
                     hostplatform.platform_tools_dir()):
        return
    app.runner.line.emit(f"# Downloading {hostplatform.platform_tools_url()}")

    def done(path):
        app.tools.refresh()
        app.runner.line.emit(f"# platform-tools installed in {path}")
        info(parent, "platform-tools", f"Installed in {path}.")
        if after:
            after()

    app.jobs.run(lambda _w: downloads.fetch_platform_tools(), done, lambda e: warn(parent, "Download failed", e))


def fetch_scrcpy(parent, app, after=None) -> None:
    if not ask_fetch(parent, "the latest Windows scrcpy release", "github.com/Genymobile/scrcpy",
                     hostplatform.scrcpy_dir()):
        return

    def done(path):
        app.tools.refresh()
        info(parent, "scrcpy", f"Installed in {path}.")
        if after:
            after()

    app.jobs.run(lambda _w: downloads.fetch_scrcpy(), done, lambda e: warn(parent, "Download failed", e))


def ask_fetch(parent, what: str, url: str, where) -> bool:
    from PyQt6.QtWidgets import QMessageBox
    return QMessageBox.question(parent, "Download", f"Download {what}?\n\nFrom: {url}\nInto: {where}\n\n"
                                                   "Nothing else is installed or changed.") == \
        QMessageBox.StandardButton.Yes


def check_tool_updates(parent, app) -> None:
    def work(_w):
        app.tools.detect_versions(app.runner)
        return downloads.latest_platform_tools_version(), app.tools.status("fastboot")

    def done(res):
        latest, fb = res
        if latest is None:
            warn(parent, "Tool updates", "Could not read Google's current platform-tools version.")
            return
        have = fb.version
        if have and parsers.version_at_least(have, ".".join(map(str, latest))):
            info(parent, "Tool updates", f"fastboot {parsers.version_str(have)} is current.")
        else:
            info(parent, "Tool updates", f"Current platform-tools: {parsers.version_str(latest)}. You have "
                                         f"{parsers.version_str(have)} ({fb.source or 'not found'}). Use Fetch / "
                                         "update platform-tools to get the current one.")

    app.jobs.run(work, done, lambda e: warn(parent, "Tool updates", e))


class UsbHelpDialog(QDialog):
    """Linux: udev rules and groups, shown as commands. Windows: driver steps. Never runs as root."""

    def __init__(self, parent, app, manufacturer: str = "", in_bootloader: bool = False):
        super().__init__(parent)
        self.app = app
        self.manufacturer = manufacturer
        self.in_bootloader = in_bootloader
        self.setWindowTitle("USB access")
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        lay.addWidget(self.box, 1)
        row = QHBoxLayout()
        copy = QPushButton("&Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.box.toPlainText()))
        again = QPushButton("Check &again")
        again.clicked.connect(self.refresh)
        row.addWidget(copy)
        if hostplatform.IS_WINDOWS:
            page = QPushButton("Open &driver page")
            page.clicked.connect(lambda: open_url(usbhelp.windows_driver_page(self.manufacturer)))
            row.addWidget(page)
            z = QPushButton("&Zadig (Samsung heimdall)")
            z.clicked.connect(lambda: open_url(usbhelp.ZADIG_URL))
            row.addWidget(z)
        row.addWidget(again)
        row.addStretch(1)
        close = QPushButton("C&lose")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.refresh()
        self.resize(820, 560)

    def refresh(self) -> None:
        if hostplatform.IS_WINDOWS:
            self.head.setText("Windows has no udev. When a phone doesn't show up, it's a driver.")
            self.box.setPlainText(usbhelp.windows_driver_steps(self.manufacturer, self.in_bootloader) + "\n\n" +
                                  usbhelp.zadig_steps())
        else:
            a = usbhelp.udev_advice()
            state = []
            state.append(f"Rules file: {a.rules_found or 'none found'}")
            state.append(f"Package manager: {a.package_manager or 'unknown'}")
            state.append(f"Group: {a.group} ({'you are in it' if a.in_group else 'you are NOT in it'})")
            self.head.setText("A normal user needs udev rules to reach the phone over USB. deADBoot never asks for "
                              "root and never writes outside your home folder: run these yourself.\n" +
                              "  |  ".join(state))
            if a.rules_found and a.in_group:
                self.box.setPlainText("Rules and group look fine.\n\nIf the phone still says 'no permissions': unplug "
                                      "and replug it, try another port or cable, and log out and back in if you "
                                      "were added to the group recently.\n\n" + a.commands)
            else:
                self.box.setPlainText(a.commands)
        if self.app.devices:
            self.app.devices.poll()


class WirelessDialog(QDialog):
    """Pair, connect and discover over Wireless Debugging, or tcpip 5555 for older phones."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Wireless debugging")
        lay = QVBoxLayout(self)
        intro = QLabel("Android 11+: Developer options, Wireless debugging, Pair device with pairing code. Enter the "
                       "IP:port and code it shows, then connect to the IP:port on the main Wireless debugging "
                       "screen (it differs from the pairing port). Use a Wi-Fi network you trust.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.pair_addr = QLineEdit()
        self.pair_addr.setPlaceholderText("192.168.1.40:37811")
        self.code = QLineEdit()
        self.code.setPlaceholderText("6-digit code")
        pair = QPushButton("&Pair")
        pr = QHBoxLayout()
        pr.addWidget(self.pair_addr, 1)
        pr.addWidget(self.code)
        pr.addWidget(pair)
        form.addRow("Pair", pr)
        self.conn_addr = QLineEdit()
        self.conn_addr.setPlaceholderText("192.168.1.40:41235")
        conn = QPushButton("&Connect")
        disc = QPushButton("&Disconnect")
        cr = QHBoxLayout()
        cr.addWidget(self.conn_addr, 1)
        cr.addWidget(conn)
        cr.addWidget(disc)
        form.addRow("Connect", cr)
        lay.addLayout(form)
        row = QHBoxLayout()
        scan = QPushButton("Discover over &mDNS")
        self.tcpip = QPushButton("Older phone: &tcpip 5555 over the cable")
        row.addWidget(scan)
        row.addWidget(self.tcpip)
        row.addStretch(1)
        lay.addLayout(row)
        self.found = QListWidget()
        lay.addWidget(self.found)
        self.out = QLabel("")
        self.out.setWordWrap(True)
        lay.addWidget(self.out)
        pair.clicked.connect(self.pair)
        conn.clicked.connect(lambda: self.run(["connect", self.conn_addr.text().strip()]))
        disc.clicked.connect(lambda: self.run(["disconnect", self.conn_addr.text().strip()]))
        scan.clicked.connect(self.scan)
        self.tcpip.clicked.connect(self.tcpip_mode)
        self.found.itemDoubleClicked.connect(self.use_found)
        self.resize(700, 480)

    def run(self, args: list[str], serial: str | None = None) -> None:
        def done(r):
            self.out.setText(r.text or f"exit {r.code}")
            self.app.devices.poll()
        self.app.jobs.run(lambda _w: self.app.runner.run_blocking(Command("adb", args, serial, timeout_s=30)), done)

    def pair(self) -> None:
        addr, code = self.pair_addr.text().strip(), self.code.text().strip()
        if addr and code:
            self.run(["pair", addr, code])

    def scan(self) -> None:
        def done(r):
            self.found.clear()
            for s in parsers.parse_mdns_services(r.out):
                kind = "pairing" if "pairing" in s.kind else "connect"
                self.found.addItem(f"{kind}  {s.address}  {s.name}")
            if not self.found.count():
                self.out.setText("Nothing found. The phone has to be on the same network with Wireless debugging on. "
                                 "Some routers block mDNS between clients.")
        self.app.jobs.run(lambda _w: self.app.runner.run_blocking(Command("adb", ["mdns", "services"], timeout_s=15),
                                                                  quiet=True), done)

    def use_found(self, item) -> None:
        kind, addr = item.text().split()[:2]
        (self.pair_addr if kind == "pairing" else self.conn_addr).setText(addr)

    def tcpip_mode(self) -> None:
        dev = self.app.current
        if not dev or dev.is_tcp or not dev.adb_ready:
            warn(self, "tcpip", "Select a phone connected over USB first.")
            return
        ip = ""
        props = dev.props
        for key in ("dhcp.wlan0.ipaddress",):
            ip = props.get(key, "")

        def work(_w):
            r = self.app.runner.run_blocking(Command("adb", ["shell", "ip -o -4 addr show wlan0"], dev.serial,
                                                     timeout_s=10), quiet=True)
            import re
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", r.out)
            self.app.runner.run_blocking(Command("adb", ["tcpip", "5555"], dev.serial, writes=True))
            return m.group(1) if m else ip

        def done(addr):
            if addr:
                self.conn_addr.setText(f"{addr}:5555")
                self.out.setText(f"adb is listening on {addr}:5555. Unplug the cable, then Connect.")
            else:
                self.out.setText("adb is listening on port 5555. Find the phone's Wi-Fi IP in its settings and "
                                 "connect to IP:5555.")

        self.app.jobs.run(work, done)
