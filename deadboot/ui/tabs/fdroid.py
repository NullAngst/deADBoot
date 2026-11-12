"""F-Droid Setup tab: Shizuku plus an F-Droid client that installs and updates apps on the phone itself."""

from __future__ import annotations

from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                             QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from ... import downloads, hostplatform, shizukuguide as guide
from ...ops import SHIZUKU_PKG
from ..common import Tab, confirm_device, info, para, warn


class FdroidTab(Tab):
    title = "F-Droid Setup"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        lay.addWidget(para(guide.INTRO))

        box = QGroupBox("Client")
        bl = QHBoxLayout(box)
        self.droidify = QRadioButton("&Droid-ify (F-Droid apps)")
        self.droidify.setChecked(True)
        self.obtainium = QRadioButton("&Obtainium (GitHub and other release pages)")
        self.iwo = QCheckBox("Also install Install &With Options")
        self.iwo.setToolTip("An on-device APK installer that also uses Shizuku, for APK files you download "
                            "yourself.")
        self.client_group = QButtonGroup(self)
        self.client_group.addButton(self.droidify)
        self.client_group.addButton(self.obtainium)
        for w in (self.droidify, self.obtainium, self.iwo):
            bl.addWidget(w)
        bl.addStretch(1)
        lay.addWidget(box)

        mbox = QGroupBox("Shizuku mode")
        ml = QHBoxLayout(mbox)
        self.usb_mode = QRadioButton("&USB cable (no root)")
        self.root_mode = QRadioButton("&Root mode (starts itself at boot)")
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

        sbox = QGroupBox("Steps 1 to 3 (deADBoot does these over the cable)")
        grid = QGridLayout(sbox)
        grid.setColumnStretch(1, 1)
        self.get_shizuku = self.gate(QPushButton("&1. Get Shizuku"), {"device"})
        self.get_client = self.gate(QPushButton("&2. Install the client"), {"device"})
        self.start_btn = self.gate(QPushButton("&3. Start Shizuku"), {"device"})
        self.status_btn = self.gate(QPushButton("Check &status"), {"device"})
        for row, (btn, key) in enumerate(((self.get_shizuku, "shizuku"), (self.get_client, "client"),
                                          (self.start_btn, "start"), (self.status_btn, "status"))):
            grid.addWidget(btn, row, 0)
            grid.addWidget(para(guide.STEP_HELP[key]), row, 1)
        lay.addWidget(sbox)

        self.status = para("", selectable=True)
        self.status.setObjectName("fdroidstatus")
        lay.addWidget(self.status)

        open_row = QHBoxLayout()
        self.open_shizuku = self.gate(QPushButton("Open Shi&zuku on the phone"), {"device"})
        self.open_client = self.gate(QPushButton("Open the c&lient on the phone"), {"device"})
        open_row.addWidget(self.open_shizuku)
        open_row.addWidget(self.open_client)
        open_row.addStretch(1)
        lay.addLayout(open_row)

        for text in (guide.CONNECT, guide.USING, guide.REBOOTS, guide.WIRELESS):
            lay.addWidget(para(text))
        lay.addStretch(1)

        self.get_shizuku.clicked.connect(self.install_shizuku)
        self.get_client.clicked.connect(self.install_client)
        self.start_btn.clicked.connect(self.start_shizuku)
        self.status_btn.clicked.connect(self.check_status)
        self.check_root_btn.clicked.connect(self.check_root)
        self.open_shizuku.clicked.connect(lambda: self.open_app([SHIZUKU_PKG], "Shizuku"))
        self.open_client.clicked.connect(self.open_client_app)

    # ------------------------------------------------------------ helpers

    def client(self) -> str:
        return "obtainium" if self.obtainium.isChecked() else "droidify"

    def client_label(self) -> str:
        return guide.CLIENT_LABELS[self.client()]

    @staticmethod
    def names(key: str) -> list[str]:
        return downloads.package_names(downloads.load_sources().get(key, {}))

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
            self.status.setText("Root detected. Root mode is selected; switch to USB cable mode if you'd rather not.")
        else:
            self.status.setText("No root granted. Shizuku will be started over the USB cable.")

    def open_app(self, packages: list[str], label: str) -> None:
        def work(w):
            ops = self.app.ops(w)
            pkg = ops.installed_variant(packages)
            if not pkg:
                return None
            return pkg, ops.launch_app(pkg)

        def done(res):
            if res is None:
                warn(self, label, f"{label} isn't installed on the phone yet.")
            elif not res[1].ok and not res[1].dry:
                warn(self, label, res[1].text[-800:] or f"Could not open {label}.")

        self.job(work, done)

    def open_client_app(self) -> None:
        self.open_app(self.names(self.client()), self.client_label())

    # ------------------------------------------------------------ steps

    def install_shizuku(self) -> None:
        dev = self.dev

        def work(w):
            ops = self.app.ops(w)
            if ops.package_installed(SHIZUKU_PKG):
                return "installed", None
            if ops.has_play():
                r = ops.open_play_listing(SHIZUKU_PKG)
                if not r.ok:
                    return "play-failed", r
                w.progress.emit("Shizuku's Play listing is open on the phone. Tap Install there. Waiting up to "
                                "five minutes for it to finish...")
                return ("play-ok" if ops.wait_for_package(SHIZUKU_PKG, timeout=300) else "play-timeout"), None
            w.progress.emit("No Play Store on this phone. Downloading Shizuku's latest GitHub release...")
            fetched = downloads.fetch_source("shizuku", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
            self.app.runner.line.emit(f"# Shizuku {fetched.version}: {fetched.detail}")
            return "github", (fetched, ops.install([str(fetched.path)], reinstall=True))

        def done(res):
            kind, r = res
            if kind == "installed":
                self.status.setText("Shizuku is already installed. Next: 2. Install the client.")
            elif kind == "play-ok":
                self.status.setText("Shizuku is installed from Play, which keeps it updated. Next: 2. Install the "
                                    "client.")
            elif kind == "play-timeout":
                self.status.setText("Shizuku still isn't installed. Tap Install on the Play listing, then press "
                                    "1. Get Shizuku again.")
            elif kind == "play-failed":
                warn(self, "Shizuku", "Could not open the Play listing:\n" + r.text[-1200:])
            else:
                fetched, res2 = r
                if res2.ok:
                    self.status.setText(f"Shizuku {fetched.version} installed from GitHub releases "
                                        f"({fetched.detail}). Next: 2. Install the client.")
                else:
                    warn(self, "Shizuku", res2.text[-1500:])

        if confirm_device(self, dev, "Get Shizuku onto the phone (its Play listing if Play is present, otherwise "
                                     "the latest GitHub release)"):
            self.status.setText("Checking the phone...")
            self.app.jobs.run(work, done, self.job_failed, self.status.setText)

    def install_client(self) -> None:
        keys = [self.client()] + (["installwithoptions"] if self.iwo.isChecked() else [])
        labels = [guide.CLIENT_LABELS.get(k, "Install With Options") for k in keys]
        if not confirm_device(self, self.dev, "Download and install: " + ", ".join(labels)):
            return

        def work(w):
            ops = self.app.ops(w)
            srcs = downloads.load_sources()
            out = []
            for k, label in zip(keys, labels):
                have = ops.installed_variant(downloads.package_names(srcs[k]))
                if have:
                    build = "F-Droid build" if have.endswith(".fdroid") else "installed"
                    out.append(f"{label}: already on the phone ({build}, {have}). Left alone.")
                    continue
                w.progress.emit(f"Downloading {label}...")
                fetched = downloads.fetch_source(k, hostplatform.cache_dir() / "apks", hostplatform.cache_dir(),
                                                 ops.abi())
                r = ops.install([str(fetched.path)], reinstall=True)
                if r.ok and not r.dry and not ops.installed_variant(downloads.package_names(srcs[k])):
                    out.append(f"{label} {fetched.version}: install reported success but the package isn't "
                               f"on the phone. Check the log.")
                else:
                    out.append(f"{label} {fetched.version}: {'installed' if r.ok else 'FAILED'} ({fetched.detail})")
            return out

        def done(lines):
            self.status.setText("<br>".join(lines) + "<br>Next: 3. Start Shizuku.")

        self.status.setText("Checking the phone...")
        self.app.jobs.run(work, done, self.job_failed, self.status.setText)

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
                self.status.setText(
                    "Shizuku is running" + (" in root mode." if root else ". It stops at the next reboot.") +
                    f"<br>Next, on the phone: {guide.connect_steps(self.client())} Open the client on the "
                    "phone does the first part for you.")
            else:
                warn(self, "Shizuku", (r.err or r.out)[-1500:])

        if confirm_device(self, self.dev, "Start the Shizuku service" + (" in root mode" if root else "")):
            self.job(work, done)

    def check_status(self) -> None:
        client, label = self.client(), self.client_label()

        def work(w):
            ops = self.app.ops(w)
            have_client = ops.installed_variant(self.names(client))
            return {
                "Shizuku installed": "yes" if ops.package_installed(SHIZUKU_PKG) else "no",
                "Shizuku running": "yes" if ops.shizuku_running() else "no",
                f"{label} installed": (f"yes ({have_client})" if have_client else "no"),
                "Play Store present": "yes" if ops.has_play() else "no",
            }

        self.job(work, lambda st: self.status.setText(
            "  |  ".join(f"{k}: {v}" for k, v in st.items())))

