"""F-Droid Setup tab: Shizuku plus an F-Droid client that installs and updates apps on the phone itself."""

from __future__ import annotations

from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton,
                             QVBoxLayout)

from ... import downloads, hostplatform
from ...ops import SHIZUKU_PKG
from ..common import Tab, confirm_device, info, warn

CAVEATS = (
    "<b>Before you start</b><ul>"
    "<li>Shizuku's service stops on reboot unless the device is rooted. Without root, re-run Start Shizuku over "
    "the cable, or restart it from Shizuku's own app over Wireless Debugging, after each reboot.</li>"
    "<li>Wireless Debugging needs Android 11 or newer. Older phones can run Shizuku over the cable; they just "
    "don't get the cable-free restart.</li>"
    "<li>On a rooted phone Shizuku can run in root mode, which survives reboots.</li></ul>")

WIRELESS = (
    "<b>Cable-free restarts (Android 11+)</b><ol>"
    "<li>Connect the phone to a Wi-Fi network you trust.</li>"
    "<li>Settings, System, Developer options, turn on Wireless debugging.</li>"
    "<li>Open Shizuku, choose Start via Wireless debugging, then Pairing. Shizuku shows a notification.</li>"
    "<li>In Developer options, Wireless debugging, Pair device with pairing code. Enter the code in "
    "Shizuku's notification.</li>"
    "<li>Back in Shizuku, tap Start. After a reboot you repeat only this last step, on the phone.</li></ol>")


class FdroidTab(Tab):
    title = "F-Droid Setup"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        lay = QVBoxLayout(self)
        caveats = QLabel(CAVEATS)
        caveats.setWordWrap(True)
        lay.addWidget(caveats)

        box = QGroupBox("Client")
        bl = QHBoxLayout(box)
        self.droidify = QRadioButton("&Droid-ify")
        self.droidify.setChecked(True)
        self.obtainium = QRadioButton("&Obtainium")
        self.iwo = QCheckBox("Also install Install &With Options")
        self.client_group = QButtonGroup(self)
        self.client_group.addButton(self.droidify)
        self.client_group.addButton(self.obtainium)
        for w in (self.droidify, self.obtainium, self.iwo):
            bl.addWidget(w)
        bl.addStretch(1)
        lay.addWidget(box)

        mbox = QGroupBox("Shizuku mode")
        ml = QHBoxLayout(mbox)
        self.usb_mode = QRadioButton("&USB / Wireless Debugging (no root)")
        self.root_mode = QRadioButton("&Root mode (survives reboots)")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.usb_mode)
        self.mode_group.addButton(self.root_mode)
        self.usb_mode.setChecked(True)
        self.root_mode.setVisible(False)
        self.check_root_btn = self.gate(QPushButton("Check for &root"), {"device"})
        for w in (self.usb_mode, self.root_mode, self.check_root_btn):
            ml.addWidget(w)
        ml.addStretch(1)
        lay.addWidget(mbox)

        steps = QHBoxLayout()
        self.get_shizuku = self.gate(QPushButton("&1. Get Shizuku"), {"device"})
        self.get_client = self.gate(QPushButton("&2. Install the client"), {"device"})
        self.start_btn = self.gate(QPushButton("&3. Start Shizuku"), {"device"})
        self.status_btn = self.gate(QPushButton("Check &status"), {"device"})
        for w in (self.get_shizuku, self.get_client, self.start_btn, self.status_btn):
            steps.addWidget(w)
        steps.addStretch(1)
        lay.addLayout(steps)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        todo = QLabel("<b>4. On the phone</b>, which deADBoot can't do for you: in the client's settings set the "
                      "installer to Shizuku, then tap Allow on the Shizuku permission prompt the client raises. "
                      "That's the end of setup. If permission isn't granted, the client says so the first time it "
                      "installs something.")
        todo.setWordWrap(True)
        lay.addWidget(todo)
        wl = QLabel(WIRELESS)
        wl.setWordWrap(True)
        lay.addWidget(wl)
        lay.addStretch(1)

        self.get_shizuku.clicked.connect(self.install_shizuku)
        self.get_client.clicked.connect(self.install_client)
        self.start_btn.clicked.connect(self.start_shizuku)
        self.status_btn.clicked.connect(self.check_status)
        self.check_root_btn.clicked.connect(self.check_root)

    def client(self) -> str:
        return "obtainium" if self.obtainium.isChecked() else "droidify"

    def on_device(self, dev) -> None:
        self.root_mode.setVisible(False)
        self.usb_mode.setChecked(True)
        self.status.setText("")
        if dev and dev.adb_ready and dev.root:
            self.check_root()

    def check_root(self) -> None:
        self.status.setText("Checking root. If Magisk or another root manager asks on the phone, allow it.")
        self.job(lambda w: self.app.ops(w).root_available(), self.root_result)

    def root_result(self, rooted: bool) -> None:
        self.root_mode.setVisible(rooted)
        if rooted:
            self.root_mode.setChecked(True)
            self.status.setText("Root detected. Root mode is selected; switch to USB mode if you'd rather not.")
        else:
            self.status.setText("No root granted. Shizuku will run in USB / Wireless Debugging mode.")

    def install_shizuku(self) -> None:
        dev = self.dev

        def work(w):
            ops = self.app.ops(w)
            if ops.package_installed(SHIZUKU_PKG):
                return "installed", None
            if ops.has_play():
                return "play", ops.open_play_listing(SHIZUKU_PKG)
            fetched = downloads.fetch_source("shizuku", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
            self.app.runner.line.emit(f"# Shizuku {fetched.version}: {fetched.detail}")
            return "github", (fetched, ops.install([str(fetched.path)], reinstall=True))

        def done(res):
            kind, r = res
            if kind == "installed":
                info(self, "Shizuku", "Shizuku is already installed. Skipping.")
            elif kind == "play":
                info(self, "Shizuku", "Shizuku's Play listing is open on the phone. Tap Install there. Play keeps "
                                      "it updated from now on.")
            else:
                fetched, res2 = r
                if res2.ok:
                    info(self, "Shizuku", f"Shizuku {fetched.version} installed from GitHub releases "
                                          f"({fetched.detail}).")
                else:
                    warn(self, "Shizuku", res2.text[-1500:])

        if confirm_device(self, dev, "Get Shizuku onto the phone (Play listing if Play is present, otherwise "
                                     "the latest GitHub release)"):
            self.job(work, done)

    def install_client(self) -> None:
        keys = [self.client()] + (["installwithoptions"] if self.iwo.isChecked() else [])
        if not confirm_device(self, self.dev, "Download and install: " + ", ".join(keys)):
            return

        def work(w):
            ops = self.app.ops(w)
            srcs = downloads.load_sources()
            out = []
            for k in keys:
                if ops.package_installed(srcs[k]["package"]):
                    out.append(f"{k}: already installed")
                    continue
                fetched = downloads.fetch_source(k, hostplatform.cache_dir() / "apks", hostplatform.cache_dir(),
                                                 ops.abi())
                r = ops.install([str(fetched.path)], reinstall=True)
                out.append(f"{k} {fetched.version}: {'installed' if r.ok else 'FAILED'} ({fetched.detail})")
            return out

        self.job(work, lambda lines: info(self, "Client", "\n".join(lines)))

    def start_shizuku(self) -> None:
        if not self.dev or not self.dev.adb_ready:
            return
        root = self.root_mode.isVisible() and self.root_mode.isChecked()

        def work(w):
            return self.app.ops(w).shizuku_start(root=root)

        def done(r):
            if r.dry:
                info(self, "Dry run", "Printed, not run.")
            elif r.ok:
                self.status.setText("Shizuku is running" + (" in root mode." if root else
                                                             ". It stops at the next reboot."))
            else:
                warn(self, "Shizuku", (r.err or r.out)[-1500:])

        if confirm_device(self, self.dev, "Start the Shizuku service" + (" in root mode" if root else "")):
            self.job(work, done)

    def check_status(self) -> None:
        def work(w):
            ops = self.app.ops(w)
            srcs = downloads.load_sources()
            return {
                "Shizuku installed": ops.package_installed(SHIZUKU_PKG),
                "Shizuku running": ops.shizuku_running(),
                f"{self.client()} installed": ops.package_installed(srcs[self.client()]["package"]),
                "Play Store present": ops.has_play(),
            }

        self.job(work, lambda st: self.status.setText(
            "  |  ".join(f"{k}: {'yes' if v else 'no'}" for k, v in st.items())))
