"""Backup tab: adb backup builder, .ab extraction, and one-click rescue sets."""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout)

from ... import abfile, parsers
from ...runner import Command
from ..common import Tab, info, pick_dir, pick_file, warn


class BackupTab(Tab):
    title = "Backup"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        lay = QVBoxLayout(self)

        box = QGroupBox("adb backup")
        bl = QVBoxLayout(box)
        note = QLabel("adb backup is deprecated. Many apps opt out of it, and on Android 12 and newer it backs up "
                      "almost nothing. It still works on the old devices this is for. The phone asks you to "
                      "confirm, and optionally set a password, on its screen.")
        note.setWordWrap(True)
        bl.addWidget(note)
        row = QHBoxLayout()
        self.apk = QCheckBox("Include APKs (-apk)")
        self.apk.setChecked(True)
        self.shared = QCheckBox("Shared storage (-shared)")
        self.all = QCheckBox("All apps (-all)")
        self.all.setChecked(True)
        self.nosystem = QCheckBox("Skip system apps (-nosystem)")
        self.nosystem.setChecked(True)
        for w in (self.apk, self.shared, self.all, self.nosystem):
            row.addWidget(w)
        row.addStretch(1)
        bl.addLayout(row)
        row2 = QHBoxLayout()
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("Or one package, e.g. com.example.app (untick All apps)")
        row2.addWidget(self.pkg, 1)
        self.run_btn = self.gate(QPushButton("&Back up..."), {"device"})
        row2.addWidget(self.run_btn)
        bl.addLayout(row2)
        lay.addWidget(box)

        ebox = QGroupBox("Extract a .ab file")
        el = QHBoxLayout(ebox)
        enote = QLabel("Unencrypted backups unpack directly. Encrypted ones need abe (Android Backup "
                       "Extractor) and the password.")
        enote.setWordWrap(True)
        el.addWidget(enote, 1)
        self.extract_btn = QPushButton("&Extract .ab...")
        el.addWidget(self.extract_btn)
        lay.addWidget(ebox)

        rbox = QGroupBox("Rescue sets")
        rl = QHBoxLayout(rbox)
        self.r_all = self.gate(QPushButton("All of /&sdcard..."), {"device", "recovery"})
        self.r_media = self.gate(QPushButton("&DCIM and Pictures..."), {"device", "recovery"})
        self.r_apks = self.gate(QPushButton("All user &app APKs..."), {"device"})
        for w in (self.r_all, self.r_media, self.r_apks):
            rl.addWidget(w)
        rl.addStretch(1)
        lay.addWidget(rbox)
        lay.addStretch(1)

        self.run_btn.clicked.connect(self.backup)
        self.extract_btn.clicked.connect(self.extract)
        self.r_all.clicked.connect(lambda: self.files_tab().rescue(["/sdcard"]))
        self.r_media.clicked.connect(lambda: self.files_tab().rescue(["/sdcard/DCIM", "/sdcard/Pictures"]))
        self.r_apks.clicked.connect(self.pull_all_apks)

    def files_tab(self):
        tab = self.app.window.tabs_by_name["Files"]
        self.app.window.show_tab(tab)
        return tab

    def backup(self) -> None:
        dev = self.dev
        name = f"backup-{(dev.codename or dev.serial)}-{_dt.datetime.now():%Y%m%d-%H%M%S}.ab"
        path, _ = QFileDialog.getSaveFileName(self, "Save backup as", str(Path.home() / name), "Android backup (*.ab)")
        if not path:
            return
        args = ["backup", "-f", path]
        if self.apk.isChecked():
            args.append("-apk")
        if self.shared.isChecked():
            args.append("-shared")
        if self.all.isChecked():
            args.append("-all")
            if self.nosystem.isChecked():
                args.append("-nosystem")
        elif self.pkg.text().strip():
            args.append(self.pkg.text().strip())
        else:
            warn(self, "Backup", "Tick All apps or enter a package.")
            return
        info(self, "Backup", "Confirm the backup on the phone's screen now. deADBoot waits until it finishes.")
        serial = dev.serial
        self.job(lambda w: self.app.runner.run_blocking(Command("adb", args, serial), cancel=w.cancel),
                 lambda r: self.backup_done(r, Path(path)))

    def backup_done(self, r, path: Path) -> None:
        size = path.stat().st_size if path.is_file() else 0
        if r.ok and size > 512:
            info(self, "Backup", f"Saved {path} ({size // 1024} KB).")
        else:
            warn(self, "Backup", f"The backup is {size} bytes. Either it was declined on the phone, or the apps "
                                 "opted out. On Android 12+ this is expected.")

    def extract(self) -> None:
        src = pick_file(self, "Backup file", "Android backup (*.ab);;All files (*)")
        if not src:
            return
        dest = pick_dir(self, "Extract into which folder?")
        if not dest:
            return
        try:
            hdr = abfile.read_header(Path(src))
        except (abfile.AbError, OSError) as exc:
            warn(self, "Extract", str(exc))
            return
        password = ""
        if hdr.encryption.lower() != "none":
            if not self.app.tools.available("abe"):
                warn(self, "Extract", "This backup is encrypted. Install abe (Android Backup Extractor) or set "
                                      "its path or abe.jar in Settings.")
                return
            password, ok = QInputDialog.getText(self, "Backup password", "Password set on the phone:",
                                                QLineEdit.EchoMode.Password)
            if not ok:
                return

        def work(_w):
            tmp = Path(tempfile.mkdtemp(prefix="deadboot-ab-"))
            tar = tmp / "backup.tar"
            if password:
                r = self.app.runner.run_blocking(Command("abe", ["unpack", src, str(tar), password]), quiet=True)
                if not r.ok:
                    raise RuntimeError("abe failed: " + r.text[-500:])
            else:
                abfile.to_tar(Path(src), tar)
            n = abfile.extract_tar(tar, Path(dest))
            tar.unlink(missing_ok=True)
            return n

        self.job(work, lambda n: info(self, "Extracted", f"{n} file(s) extracted to {dest}."))

    def pull_all_apks(self) -> None:
        dest = pick_dir(self, "Save every user app's APKs into which folder? (one folder per package)")
        if not dest:
            return

        def work(w):
            ops = self.app.ops(w)
            pkgs = parsers.parse_pm_list(ops.qshell("pm list packages -3"))
            count = 0
            for p in pkgs:
                if w.cancel.is_set():
                    break
                target = Path(dest) / p.name
                target.mkdir(parents=True, exist_ok=True)
                for rp in ops.apk_paths(p.name):
                    if ops.pull(rp, str(target / rp.rsplit("/", 1)[-1])).ok:
                        count += 1
            return len(pkgs), count

        self.job(work, lambda res: info(self, "APKs", f"{res[1]} APK file(s) from {res[0]} app(s) saved to {dest}."))
