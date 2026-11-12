"""Flashing & Root tab. Walled off behind an acknowledgement, every destructive step typed-confirmed.

An assisted console over fastboot, adb sideload, heimdall and the Magisk app.
It shows each command, enforces the guardrails and reimplements none of the tools.
"""

from __future__ import annotations

import re
import shutil
import tarfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPushButton, QRadioButton, QScrollArea, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ... import downloads, hostplatform, magisk, packages, parsers, profiles
from ...devicestate import DeviceState
from ...ops import extract_zip_to, find_script_dir
from ...runner import Command
from ..common import Tab, ask, info, open_url, pick_file, show_text, typed_confirm, warn

FB = {"bootloader", "fastbootd"}
ADB = {"device", "recovery", "sideload"}
ANY = FB | ADB
COMMON_PARTITIONS = ["boot", "init_boot", "vendor_boot", "recovery", "dtbo", "vbmeta", "vbmeta_system",
                     "vendor_kernel_boot", "super", "system", "vendor", "product", "bootloader", "radio"]


def guess_partition(name: str) -> str:
    stem = Path(name).stem.lower()
    if stem in COMMON_PARTITIONS:
        return stem
    for p in sorted(COMMON_PARTITIONS, key=len, reverse=True):
        if re.search(rf"(^|[^a-z]){p}([^a-z]|$)", stem):
            return p
    return ""


def section(title: str) -> tuple[QGroupBox, QVBoxLayout]:
    box = QGroupBox(title)
    return box, QVBoxLayout(box)


def label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


class DropLabel(QLabel):
    def __init__(self, tab: "FlashTab"):
        super().__init__("Drop a ROM zip, factory image or folder of images here")
        self.tab = tab
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(56)
        self.setObjectName("dropzone")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.tab.drop_files(urls)


class FlashTab(Tab):
    title = "Flashing & Root"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.pkg: packages.PackageInfo | None = None
        self.match: packages.Match | None = None
        self.verification = "unverified"
        self.addons: list[str] = []
        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        gate = QWidget()
        gl = QVBoxLayout(gate)
        gl.addStretch(1)
        warnlab = label("<h2>Flashing and rooting can brick a device.</h2>"
                        "This tab unlocks bootloaders, flashes images, installs ROMs and roots with Magisk. "
                        "Every destructive step asks you to type the device's model name, and deADBoot blocks "
                        "flashes it can tell are wrong for the device. It cannot catch everything. A bad flash on "
                        "some devices is permanent.")
        warnlab.setProperty("danger", True)
        gl.addWidget(warnlab)
        self.ack_btn = QPushButton("I &understand this can brick a device")
        self.ack_btn.setProperty("danger", True)
        self.ack_btn.clicked.connect(self.acknowledge)
        gl.addWidget(self.ack_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        gl.addStretch(1)
        self.stack.addWidget(gate)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("flashbody")
        self.body = QVBoxLayout(body)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        self.build_state()
        self.build_files()
        self.build_package()
        self.build_install()
        self.build_bootloader()
        self.build_root()
        self.build_samsung()
        self.body.addStretch(1)
        self.render_profile()
        self.stack.setCurrentIndex(1 if app.settings.flashing_acknowledged else 0)

    def acknowledge(self) -> None:
        self.app.settings.flashing_acknowledged = True
        self.app.settings.save()
        self.stack.setCurrentIndex(1)

    # ================================================================ sections

    def build_state(self) -> None:
        box, lay = section("Device state")
        row = QHBoxLayout()
        self.dry = QCheckBox("Show me the &commands (dry run: print, don't run)")
        self.dry.toggled.connect(self.set_dry)
        row.addWidget(self.dry)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        for text, to in (("Reboot to &bootloader", "bootloader"), ("to &fastbootd", "fastbootd"),
                         ("to r&ecovery", "recovery"), ("to s&ystem", "system")):
            b = self.gate(QPushButton(text), ANY)
            b.clicked.connect(lambda _c=False, t=to: self.do_reboot(t))
            row.addWidget(b)
        self.read_btn = self.gate(QPushButton("&Read state"), ANY)
        self.read_btn.clicked.connect(self.read_state)
        row.addWidget(self.read_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.state_lab = label("No device selected.")
        lay.addWidget(self.state_lab)
        self.body.addWidget(box)

    def build_files(self) -> None:
        box, lay = section("Get the files")
        row = QHBoxLayout()
        self.profile = QComboBox()
        for pid, prof in sorted(self.app.profiles.items(), key=lambda kv: kv[1].name):
            self.profile.addItem(prof.name, pid)
        self.profile.setCurrentIndex(max(0, self.profile.findData("generic")))
        self.profile.currentIndexChanged.connect(self.render_profile)
        row.addWidget(QLabel("ROM profile:"))
        row.addWidget(self.profile, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.url_btns = {}
        for key, text in (("install_guide", "Install &guide"), ("downloads", "&Downloads"),
                          ("devices", "Supported de&vices"), ("xda", "XDA &forum")):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, k=key: open_url(self.cur_profile().url(k, self.codename())))
            self.url_btns[key] = b
            row.addWidget(b)
        self.key_btn = QPushButton("Signing &key")
        self.key_btn.clicked.connect(lambda: open_url(self.cur_profile().signature.get("key_url", "")))
        row.addWidget(self.key_btn)
        self.factory_btn = QPushButton("Pixel factory &image page")
        self.factory_btn.clicked.connect(
            lambda: open_url(f"https://developers.google.com/android/images#{self.codename()}"))
        self.ota_btn = QPushButton("Pixel &OTA page")
        self.ota_btn.clicked.connect(lambda: open_url(f"https://developers.google.com/android/ota#{self.codename()}"))
        row.addWidget(self.factory_btn)
        row.addWidget(self.ota_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.profile_lab = label("")
        lay.addWidget(self.profile_lab)
        self.body.addWidget(box)

    def build_package(self) -> None:
        box, lay = section("Package")
        self.drop = DropLabel(self)
        lay.addWidget(self.drop)
        row = QHBoxLayout()
        b1 = QPushButton("Choose &zip or image...")
        b1.clicked.connect(lambda: (p := pick_file(self, "Package", "ROM packages (*.zip *.img);;All files (*)"))
                           and self.load_package(Path(p)))
        b2 = QPushButton("Choose fo&lder...")
        b2.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(self, "Folder of images"))
                           and self.load_package(Path(d)))
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lay.addLayout(row)
        self.pkg_lab = label("No package chosen.")
        lay.addWidget(self.pkg_lab)
        row = QHBoxLayout()
        self.hash_edit = QLineEdit()
        self.hash_edit.setPlaceholderText("Paste the SHA-256 the project publishes (or load a .sha256 file)")
        hb = QPushButton("Load .sha&256...")
        hb.clicked.connect(self.load_hash_file)
        row.addWidget(self.hash_edit, 1)
        row.addWidget(hb)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.sig_edit = QLineEdit()
        self.sig_edit.setPlaceholderText("Signature file (.sig, .asc, .minisig)")
        sb = QPushButton("Si&gnature...")
        sb.clicked.connect(lambda: self.sig_edit.setText(pick_file(self, "Signature") or self.sig_edit.text()))
        self.signers_edit = QLineEdit()
        self.signers_edit.setPlaceholderText("allowed_signers or public key file")
        kb = QPushButton("&Key file...")
        kb.clicked.connect(lambda: self.signers_edit.setText(pick_file(self, "Key") or self.signers_edit.text()))
        for w in (self.sig_edit, sb, self.signers_edit, kb):
            row.addWidget(w)
        lay.addLayout(row)
        self.verify_btn = QPushButton("&Verify package")
        self.verify_btn.clicked.connect(self.verify)
        lay.addWidget(self.verify_btn)
        self.verify_lab = label("")
        lay.addWidget(self.verify_lab)
        self.body.addWidget(box)

    def build_install(self) -> None:
        box, lay = section("Install")
        self.steps_lab = label("")
        lay.addWidget(self.steps_lab)
        # factory
        row = QHBoxLayout()
        self.flashall_btn = self.gate(QPushButton("Run the package's flash-&all"), FB)
        self.keep_data = QCheckBox("Keep data (drop -w; usually fails across a downgrade or ROM switch)")
        row.addWidget(self.flashall_btn)
        row.addWidget(self.keep_data)
        row.addStretch(1)
        lay.addLayout(row)
        # fastboot update
        row = QHBoxLayout()
        self.update_btn = self.gate(QPushButton("fastboot &update"), FB)
        self.update_wipe = QCheckBox("Wipe data (-w)")
        row.addWidget(self.update_btn)
        row.addWidget(self.update_wipe)
        row.addStretch(1)
        lay.addLayout(row)
        # recovery / sideload
        row = QHBoxLayout()
        self.boot_rec_btn = self.gate(QPushButton("Boot recovery image &once..."), FB)
        self.wait_sl_btn = self.gate(QPushButton("&Wait for sideload mode"), ANY)
        self.sideload_btn = self.gate(QPushButton("&Sideload the ROM"), {"sideload", "recovery"})
        for w in (self.boot_rec_btn, self.wait_sl_btn, self.sideload_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.addon_list = QListWidget()
        self.addon_list.setMaximumHeight(70)
        self.addon_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        add = QPushButton("Add add-on zip...")
        add.clicked.connect(self.add_addon)
        rm = QPushButton("Remove")
        rm.clicked.connect(lambda: [self.addon_list.takeItem(self.addon_list.row(i))
                                    for i in self.addon_list.selectedItems()])
        self.addons_btn = self.gate(QPushButton("Sideload add-&ons in order"), {"sideload", "recovery"})
        row.addWidget(QLabel("Add-ons (Google apps, Magisk...):"))
        row.addWidget(self.addon_list, 1)
        for w in (add, rm, self.addons_btn):
            row.addWidget(w)
        lay.addLayout(row)
        # loose images
        lay.addWidget(label("Loose images: map each image to a partition. They flash top to bottom. Disable "
                            "verity only applies to vbmeta images and is ignored for anything else."))
        self.map_table = QTableWidget(0, 4)
        self.map_table.setHorizontalHeaderLabels(["Image", "Partition", "Both slots", "Disable verity (vbmeta)"])
        self.map_table.horizontalHeader().setStretchLastSection(True)
        self.map_table.setMaximumHeight(150)
        lay.addWidget(self.map_table)
        row = QHBoxLayout()
        self.flash_map_btn = self.gate(QPushButton("Flash &mapped images"), FB)
        row.addWidget(self.flash_map_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.flashall_btn.clicked.connect(self.run_flash_all)
        self.update_btn.clicked.connect(self.run_update)
        self.boot_rec_btn.clicked.connect(self.boot_recovery)
        self.wait_sl_btn.clicked.connect(self.wait_sideload)
        self.sideload_btn.clicked.connect(self.sideload_rom)
        self.addons_btn.clicked.connect(self.sideload_addons)
        self.flash_map_btn.clicked.connect(self.flash_mapped)
        # Nothing to run until a package is chosen; render_package shows the buttons that fit it.
        for w in (self.flashall_btn, self.keep_data, self.update_btn, self.update_wipe, self.sideload_btn):
            w.setVisible(False)
        self.body.addWidget(box)

    def build_bootloader(self) -> None:
        box, lay = section("Bootloader")
        lay.addWidget(label("OEM unlocking is a toggle in Developer options on the phone. It can't be flipped over "
                            "adb, and on some carrier phones it is greyed out for good. Unlocking wipes the phone "
                            "and needs a volume/power press on the device to confirm."))
        row = QHBoxLayout()
        self.unlock_btn = self.gate(QPushButton("Un&lock bootloader..."), FB)
        self.unlock_btn.setProperty("danger", True)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Unlock code (Motorola, Sony; leave empty otherwise)")
        self.lock_btn = self.gate(QPushButton("Re&lock bootloader..."), FB)
        self.lock_btn.setProperty("danger", True)
        for w in (self.unlock_btn, self.code_edit, self.lock_btn):
            row.addWidget(w)
        lay.addLayout(row)
        self.lock_lab = label("")
        lay.addWidget(self.lock_lab)
        row = QHBoxLayout()
        self.part_combo = QComboBox()
        self.part_combo.setEditable(True)
        self.part_combo.addItems(COMMON_PARTITIONS)
        self.single_btn = self.gate(QPushButton("Flash one &partition..."), FB)
        self.single_both = QCheckBox("Both slots")
        self.bootimg_btn = self.gate(QPushButton("&Boot an image without flashing..."), {"bootloader"})
        for w in (QLabel("Partition:"), self.part_combo, self.single_both, self.single_btn, self.bootimg_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.slot_a = self.gate(QPushButton("Set active slot &A"), FB)
        self.slot_b = self.gate(QPushButton("Set active slot &B"), FB)
        row.addWidget(self.slot_a)
        row.addWidget(self.slot_b)
        row.addStretch(1)
        lay.addLayout(row)
        self.unlock_btn.clicked.connect(self.unlock)
        self.lock_btn.clicked.connect(self.relock)
        self.single_btn.clicked.connect(self.flash_single)
        self.bootimg_btn.clicked.connect(self.boot_any)
        self.slot_a.clicked.connect(lambda: self.set_slot("a"))
        self.slot_b.clicked.connect(lambda: self.set_slot("b"))
        self.body.addWidget(box)

    def build_root(self) -> None:
        box, lay = section("Root (Magisk)")
        self.root_lab = label("The boot image must match the exact build on the phone. Start with the phone booted "
                              "and adb working.")
        lay.addWidget(self.root_lab)
        row = QHBoxLayout()
        self.src_device = QRadioButton("Read from the rooted phone (dd)")
        self.src_factory = QRadioButton("From a factory image zip")
        self.src_payload = QRadioButton("From the ROM zip (payload.bin)")
        self.src_factory.setChecked(True)
        grp = QButtonGroup(self)
        for w in (self.src_device, self.src_factory, self.src_payload):
            grp.addButton(w)
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.root_pkg = QLineEdit()
        self.root_pkg.setPlaceholderText("Factory zip or ROM zip for the running build")
        rp = QPushButton("Choose...")
        rp.clicked.connect(lambda: self.root_pkg.setText(pick_file(self, "Package", "Zip (*.zip)") or
                                                         self.root_pkg.text()))
        row.addWidget(self.root_pkg, 1)
        row.addWidget(rp)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.r1 = self.gate(QPushButton("&1 Read build"), {"device"})
        self.r2 = self.gate(QPushButton("&2 Pick partition"), {"device"})
        self.r2.setToolTip("Reads the partition list over adb. If the phone doesn't allow that, it reboots to the "
                           "bootloader to ask, then back to system.")
        self.r3 = self.gate(QPushButton("&3 Get stock image"), {"device"})
        self.r4 = self.gate(QPushButton("&4 Install Magisk + push image"), {"device"})
        for w in (self.r1, self.r2, self.r3, self.r4):
            row.addWidget(w)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.r5 = self.gate(QPushButton("&5 Pull patched image"), {"device"})
        self.r6 = self.gate(QPushButton("&6 Test boot (boot devices)"), {"bootloader"})
        self.r7 = self.gate(QPushButton("&7 Flash patched image"), FB)
        self.r7.setProperty("danger", True)
        self.restore_btn = self.gate(QPushButton("Restore &stock image"), FB)
        for w in (self.r5, self.r6, self.r7, self.restore_btn):
            row.addWidget(w)
        lay.addLayout(row)
        self.root_status = label("")
        lay.addWidget(self.root_status)
        self.build: magisk.BuildInfo | None = None
        self.root_part: str | None = None
        self.r1.clicked.connect(self.root_read_build)
        self.r2.clicked.connect(self.root_partition)
        self.r3.clicked.connect(self.root_stock)
        self.r4.clicked.connect(self.root_push)
        self.r5.clicked.connect(self.root_pull)
        self.r6.clicked.connect(self.root_test_boot)
        self.r7.clicked.connect(self.root_flash)
        self.restore_btn.clicked.connect(self.root_restore)
        self.body.addWidget(box)

    def build_samsung(self) -> None:
        box, lay = section("Samsung (download mode, heimdall)")
        lay.addWidget(label(
            "Limits, stated up front: upstream heimdall is barely maintained (the fork LineageOS links works on "
            "more devices). Newer Samsung devices and bootloaders fail with heimdall more often than not, and there "
            "is no Linux fix for that here. US Snapdragon models generally can't be bootloader-unlocked at all. "
            "Unlocking trips Knox permanently: Samsung Pay, Secure Folder and some banking apps stop working for "
            "good, even after relocking. deADBoot doesn't download Samsung firmware."))
        row = QHBoxLayout()
        self.h_detect = QPushButton("&Detect")
        self.h_pit = QPushButton("Print &PIT")
        self.h_extract = QPushButton("E&xtract .tar.md5...")
        self.h_part = QLineEdit()
        self.h_part.setPlaceholderText("PIT partition name, e.g. RECOVERY or BOOT")
        self.h_flash = QPushButton("&Flash file to partition...")
        self.h_flash.setProperty("danger", True)
        for w in (self.h_detect, self.h_pit, self.h_extract, self.h_part, self.h_flash):
            row.addWidget(w)
        lay.addLayout(row)
        self.h_lab = label("")
        lay.addWidget(self.h_lab)
        self.h_detect.clicked.connect(lambda: self.heimdall(["detect"], False))
        self.h_pit.clicked.connect(lambda: self.heimdall(["print-pit", "--no-reboot"], False))
        self.h_extract.clicked.connect(self.extract_tar_md5)
        self.h_flash.clicked.connect(self.heimdall_flash)
        have = self.app.tools.available("heimdall")
        for w in (self.h_detect, self.h_pit, self.h_flash):
            w.setEnabled(have)
            if not have:
                w.setToolTip("heimdall not found: install " + self.app.tools.hint("heimdall"))
        if hostplatform.IS_WINDOWS:
            from ...usbhelp import zadig_steps
            lay.addWidget(label(zadig_steps().replace("\n", "<br>")))
        self.body.addWidget(box)

    # ================================================================ helpers

    def set_dry(self, on: bool) -> None:
        self.app.runner.dry_run = on
        self.app.window.update_dry_indicator()

    def cur_profile(self):
        return self.app.profiles[self.profile.currentData() or "generic"]

    def codename(self) -> str:
        return self.dev.codename if self.dev else ""

    def state(self) -> DeviceState:
        return DeviceState(self.dev.serial)

    def on_device(self, dev) -> None:
        if dev is None:
            self.state_lab.setText("No device selected.")
            return
        vars_ = dev.fastboot_vars
        parts = [f"<b>{dev.confirm_name}</b>", f"codename {dev.codename or '?'}", f"mode {dev.state}",
                 f"bootloader {dev.lock}", f"slot {dev.slot or '?'}"]
        if dev.battery is not None:
            parts.append(f"battery {dev.battery}%")
        if vars_:
            ua = vars_.get("unlock_ability", "")
            if ua:
                parts.append("unlock ability " + ("yes" if ua == "1" else "NO (OEM unlocking off or not allowed)"))
            for k in ("version-bootloader", "anti", "version-baseband"):
                if vars_.get(k):
                    parts.append(f"{k} {vars_[k]}")
        self.state_lab.setText("  |  ".join(parts))
        self.render_profile()
        allowed, why = self.state().relock_allowed()
        self.lock_lab.setText(("Relock: allowed. " if allowed else "Relock: refused. ") + why)
        if self.pkg:
            self.render_package()

    def render_profile(self) -> None:
        if not hasattr(self, "profile_lab"):
            return
        p = self.cur_profile()
        cn = self.codename() or "<codename>"
        for key, b in self.url_btns.items():
            b.setEnabled(bool(p.url(key, cn)))
        self.key_btn.setEnabled(bool(p.signature.get("key_url")))
        pixel = p.id in ("stock", "grapheneos") or (self.dev and (self.dev.manufacturer or "").lower() == "google")
        self.factory_btn.setVisible(bool(pixel))
        self.ota_btn.setVisible(bool(pixel))
        lines = [p.description]
        if self.dev and self.dev.props.get("ro.build.id"):
            lines.append(f"Running build ID: <b>{self.dev.props['ro.build.id']}</b> (look for this on the factory "
                         "image page when you want to root).")
        lines.append("<b>Download, all from the same build:</b><br>" +
                     "<br>".join("- " + c.replace("<codename>", cn) for c in p.checklist))
        if p.pixel_only and self.dev and self.dev.manufacturer and self.dev.manufacturer.lower() != "google":
            lines.append("<b>This profile is for Pixels only.</b>")
        lines.append(f"Verification: {p.verify}. Relock after install: {'supported' if p.relock_supported else 'no'}."
                     f" Root: {'compatible' if p.root_supported else 'not supported by the project'}."
                     + (f" Needs fastboot {p.min_fastboot}+." if p.min_fastboot else ""))
        if p.xda_url:
            lines.append("Device not on the official list? Community builds on XDA vary a lot in quality.")
        self.profile_lab.setText("<br>".join(lines))
        pre = "<br>".join("Before: " + s["text"] for s in p.pre_steps)
        post = "<br>".join("After: " + s["text"] for s in p.post_steps)
        guide = p.url("install_guide", cn)
        self.steps_lab.setText(
            (f"Keep the install guide open: <a href='{guide}'>{guide}</a><br>" if guide else "") +
            (pre + "<br>" if pre else "") + (post if post else "") +
            "<br>If the guide lists a step deADBoot doesn't automate (firmware prerequisites, copy-partitions, a "
            "setting on the phone), do it, then continue here.")
        self.steps_lab.setOpenExternalLinks(True)

    def battery_ok(self, action: str) -> bool:
        level = self.dev.battery if self.dev else None
        if level is None:
            level = self.app.window.last_battery.get(self.dev.serial) if self.dev else None
        threshold = self.app.settings.battery_threshold
        if level is None:
            return ask(self, "Battery unknown", f"The battery level is unknown in this mode. A phone dying mid-flash "
                                                f"is a common way to brick. Make sure it is above {threshold}%.\n\n"
                                                "Continue?")
        if level >= threshold:
            return True
        return typed_confirm(self, self.dev, f"Battery is at {level}%, below {threshold}%. {action} anyway?",
                             expected="LOW BATTERY",
                             danger_lines=["A phone dying mid-flash is one of the common ways to brick it."])

    def guard(self, action: str, details: str = "", wipes: bool = False, need_package: bool = False,
              extra: list[str] | None = None) -> bool:
        """Every flash: device match, verification state, battery, then a typed confirmation."""
        if self.dev is None:
            return False
        danger = [e for e in (extra or []) if e]
        if need_package:
            if self.pkg is None:
                warn(self, "No package", "Choose a package first.")
                return False
            self.match_package()
            if self.match and self.match.blocked:
                warn(self, "Blocked", self.match.message)
                return False
            if self.match and self.match.checked_by != "metadata":
                danger.append(self.match.message)
            danger += self.match.warnings if self.match else []
            if self.verification.startswith("FAILED"):
                warn(self, "Blocked", "Verification failed. Not flashing.")
                return False
            if self.verification == "unverified":
                danger.append("UNVERIFIED: no hash or signature was checked for this package.")
            p = self.cur_profile()
            if p.min_fastboot:
                fb = self.app.tools.status("fastboot").version
                if not parsers.version_at_least(fb, p.min_fastboot):
                    warn(self, "fastboot too old", f"{p.name} needs fastboot {p.min_fastboot} or newer; found "
                         f"{parsers.version_str(fb)}. Fetch current platform-tools in Settings.")
                    return False
            rb = packages.rollback_warning(self.pkg, self.dev.props.get("ro.build.id", ""),
                                           self.dev.props.get("ro.build.date.utc", ""))
            if rb:
                danger.append(rb)
        if wipes:
            danger.append("This wipes the device.")
        if not self.battery_ok(action):
            return False
        return typed_confirm(self, self.dev, action, details, danger)

    def plan_text(self, fn) -> str:
        ops = self.app.ops(planning=True)
        fn(ops)
        return "\n".join(("  " if i.kind == "cmd" else "# ") + i.text for i in ops.plan)

    def run_ops(self, fn, done_text: str, after=None) -> None:
        def work(w):
            return fn(self.app.ops(w))

        def done(r):
            if after and r is not None and getattr(r, "ok", False) and not getattr(r, "dry", False):
                after()
            if r is not None:
                self.result_box(r, done_text)
            if self.dev:
                self.app.devices.refresh(self.dev.serial)

        self.job(work, done)

    # ================================================================ state and reboots

    def do_reboot(self, to: str) -> None:
        self.run_ops(lambda o: o.reboot(to), f"Device is in {to} mode.")

    def read_state(self) -> None:
        if self.dev:
            self.app.devices.refresh(self.dev.serial)

    # ================================================================ package

    def drop_files(self, paths: list[str]) -> None:
        p = Path(paths[0])
        if p.suffix.lower() in (".sig", ".asc", ".minisig"):
            self.sig_edit.setText(str(p))
            return
        if p.name == "allowed_signers" or p.suffix.lower() in (".pub", ".key"):
            self.signers_edit.setText(str(p))
            return
        self.load_package(p)
        for extra in paths[1:]:
            e = Path(extra)
            if e.suffix.lower() in (".sig", ".asc", ".minisig"):
                self.sig_edit.setText(str(e))
            elif e.name == "allowed_signers":
                self.signers_edit.setText(str(e))

    def load_package(self, path: Path) -> None:
        self.pkg_lab.setText(f"Reading {path.name}...")
        self.verification = "unverified"
        self.verify_lab.setText("")

        def done(info_):
            self.pkg = info_
            sig = Path(str(path) + self.cur_profile().signature.get("signature_suffix", ".sig"))
            if sig.is_file():
                self.sig_edit.setText(str(sig))
            self.render_package()
            self.fill_map_table()

        self.job(lambda _w: packages.detect(path), done)

    def match_package(self) -> None:
        if not self.pkg or not self.dev:
            self.match = None
            return
        v = self.dev.fastboot_vars
        self.match = packages.match_device(self.pkg, self.dev.codename, v.get("product", ""),
                                           v.get("version-bootloader", ""), v.get("version-baseband", ""))

    def render_package(self) -> None:
        pk = self.pkg
        self.match_package()
        lines = [f"<b>{pk.path.name}</b>", packages.TYPE_LABELS[pk.kind]]
        if pk.build:
            lines.append(f"Build: {pk.build}")
        if pk.target_devices:
            lines.append("Targets: " + ", ".join(pk.target_devices))
        if pk.images:
            lines.append(f"{len(pk.images)} image(s)" + (", has init_boot.img" if pk.has_init_boot else ""))
        lines += pk.notes
        if self.match:
            tag = "BLOCKED: " if self.match.blocked else ""
            lines.append(tag + self.match.message)
            lines += self.match.warnings
        elif not self.dev:
            lines.append("Select a device to check the package against it.")
        prof = self.cur_profile()
        if prof.package_types and pk.kind not in prof.package_types and pk.kind != packages.UNKNOWN:
            lines.append(f"Note: the {prof.name} profile expects {', '.join(prof.package_types)}.")
        self.pkg_lab.setText("<br>".join(lines))
        kind = pk.kind
        self.flashall_btn.setVisible(kind == packages.FACTORY)
        self.keep_data.setVisible(kind == packages.FACTORY)
        self.update_btn.setVisible(kind == packages.FASTBOOT_UPDATE)
        self.update_wipe.setVisible(kind == packages.FASTBOOT_UPDATE)
        self.sideload_btn.setVisible(kind in (packages.RECOVERY_ZIP, packages.OTA_PAYLOAD))

    def fill_map_table(self) -> None:
        """Only when a new package is loaded. render_package runs on every device refresh (each reboot,
        each Read state), and rebuilding the table there threw away the partitions the person had mapped."""
        self.map_table.setRowCount(0)
        pk = self.pkg
        if pk and pk.kind in (packages.LOOSE_IMAGES, packages.UNKNOWN, packages.FASTBOOT_UPDATE):
            for img in pk.images:
                self.add_map_row(img)

    def add_map_row(self, img: str) -> None:
        r = self.map_table.rowCount()
        self.map_table.insertRow(r)
        self.map_table.setItem(r, 0, QTableWidgetItem(img))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems([""] + COMMON_PARTITIONS)
        combo.setCurrentText(guess_partition(img))
        self.map_table.setCellWidget(r, 1, combo)
        both = QCheckBox()
        self.map_table.setCellWidget(r, 2, both)
        verity = QCheckBox()
        self.map_table.setCellWidget(r, 3, verity)

    def load_hash_file(self) -> None:
        p = pick_file(self, "Checksum file", "Checksums (*.sha256 *.sha256sum *.txt);;All files (*)")
        if p:
            self.hash_edit.setText(downloads.normalize_hash(Path(p).read_text(errors="replace")))

    def verify(self) -> None:
        if not self.pkg or not self.pkg.path.is_file() and not self.pkg.path.is_dir():
            warn(self, "Verify", "Choose a package first.")
            return
        prof = self.cur_profile()
        want = downloads.normalize_hash(self.hash_edit.text())
        sig = self.sig_edit.text().strip()
        signers = self.signers_edit.text().strip()
        target = self.pkg.path

        def work(_w):
            results = []
            if want and target.is_file():
                got = downloads.sha256_file(target)
                results.append(("hash", got == want, f"SHA-256 {'matches' if got == want else 'DOES NOT MATCH'}"))
            if sig and target.is_file():
                results.append(("sig",) + self.check_signature(prof, target, Path(sig), Path(signers) if signers
                                                                 else None))
            return results

        def done(results):
            if not results:
                self.verification = "unverified"
                self.verify_lab.setText("Nothing to check: no hash entered and no signature chosen. The package is "
                                        "unverified.")
                return
            bad = [m for _k, ok, m in results if not ok]
            self.verification = ("FAILED: " + "; ".join(bad)) if bad else \
                "verified: " + "; ".join(m for _k, _ok, m in results)
            self.verify_lab.setText(self.verification)

        self.job(work, done)

    def check_signature(self, prof, target: Path, sig: Path, key: Path | None) -> tuple[bool, str]:
        method = prof.verify
        runner = self.app.runner
        if method == "ssh-keygen" or sig.suffix == ".sig" and key and key.name == "allowed_signers":
            if not key or not key.is_file():
                return False, "allowed_signers file not chosen"
            if not self.app.tools.ssh_keygen_can_verify():
                return False, "ssh-keygen with -Y verify (OpenSSH 8.1+) is needed"
            changed = self.track_key(prof.id, key)
            r = runner.run_blocking(Command("ssh-keygen", [
                "-Y", "verify", "-f", str(key), "-I", prof.signature.get("identity", ""),
                "-n", prof.signature.get("namespace", "file"), "-s", str(sig)], stdin_file=str(target)))
            expect = prof.signature.get("expect", "Good")
            ok = r.ok and expect in r.text
            msg = ("Signature good" if ok else "SIGNATURE CHECK FAILED") + (
                ". NOTE: allowed_signers differs from the copy saved last time" if changed else "")
            return ok, msg
        if method == "gpg" or sig.suffix == ".asc":
            r = runner.run_blocking(Command("gpg", ["--verify", str(sig), str(target)]))
            return r.ok, "GPG signature good" if r.ok else "GPG SIGNATURE CHECK FAILED"
        if method == "minisign" or sig.suffix == ".minisig":
            if not key:
                return False, "minisign public key file not chosen"
            r = runner.run_blocking(Command("minisign", ["-V", "-m", str(target), "-x", str(sig), "-p", str(key)]))
            return r.ok, "minisign signature good" if r.ok else "MINISIGN CHECK FAILED"
        return False, f"profile {prof.name} has no signature method"

    def track_key(self, profile_id: str, key: Path) -> bool:
        """Keep a copy of allowed_signers; report when the supplied one differs from the saved copy."""
        saved = hostplatform.keys_dir() / profile_id / key.name
        new = key.read_bytes()
        changed = saved.is_file() and saved.read_bytes() != new
        saved.parent.mkdir(parents=True, exist_ok=True)
        if not saved.is_file():
            saved.write_bytes(new)
        elif changed:
            self.app.runner.line.emit(f"! {key.name} differs from the copy saved in {saved}. If the project did "
                                      "not announce a key change, stop here.")
        return changed

    # ================================================================ install flows

    def record_rom(self) -> None:
        p = self.cur_profile()
        kind = self.pkg.kind if self.pkg else ""
        safe = profiles.relock_safe(p, kind, self.verification.startswith("verified"))
        self.state().record_flash(p.id, p.kind, safe, self.pkg.path.name if self.pkg else "")
        if p.relock_supported and not safe:
            self.app.runner.line.emit(f"# Relock stays disabled: the package wasn't verified, or its type ({kind}) "
                                      f"isn't one the {p.name} profile installs.")

    def run_flash_all(self) -> None:
        pk = self.pkg
        if pk is None or pk.kind != packages.FACTORY:
            return
        keep = self.keep_data.isChecked()
        plan = self.plan_text(lambda o: o.flash_all("<extracted package>", keep))
        if not self.guard(f"Flash {pk.path.name} with its own {hostplatform.flash_all_script_name()}", plan,
                          wipes=not keep, need_package=True):
            return

        def fn(o):
            src = pk.path
            dest = None
            if src.is_dir():
                d = find_script_dir(src)
            else:
                dest = hostplatform.cache_dir() / "extracted" / src.stem
                o.status(f"Extracting {src.name}")
                extract_zip_to(src, dest)
                d = find_script_dir(dest)
            try:
                if not d:
                    raise RuntimeError(f"No {hostplatform.flash_all_script_name()} found in the package")
                return o.flash_all(str(d), keep)
            finally:
                if dest is not None:
                    # A factory image unpacks to several GB; the original zip is still there for a retry.
                    shutil.rmtree(dest, ignore_errors=True)

        self.run_ops(fn, "flash-all finished. Check the log for the script's own output.", self.record_rom)

    def run_update(self) -> None:
        if self.pkg is None or self.pkg.kind != packages.FASTBOOT_UPDATE:
            return
        if self.pkg.path.is_dir():
            warn(self, "fastboot update", "fastboot update takes a zip, not a folder. Use the loose image table "
                                          "below to flash the folder's images one by one.")
            return
        wipe = self.update_wipe.isChecked()
        path = str(self.pkg.path)
        if not self.guard(f"fastboot update {self.pkg.path.name}", self.plan_text(lambda o: o.fastboot_update(path, wipe)),
                          wipes=wipe, need_package=True):
            return
        self.run_ops(lambda o: o.fastboot_update(path, wipe), "fastboot update finished.", self.record_rom)

    def boot_recovery(self) -> None:
        img = pick_file(self, "Recovery image from the same build as the ROM", "Images (*.img);;All files (*)")
        if not img:
            return
        m = packages.match_device(packages.detect(Path(img)), self.codename())
        if m.blocked:
            warn(self, "Blocked", m.message)
            return
        if not self.guard(f"Boot {Path(img).name} once (nothing is written)", f"fastboot boot {img}",
                          extra=[danger_lines_extra(m)]):
            return
        self.run_ops(lambda o: o.boot_image(img), "Booted. In recovery choose Apply update, Apply from ADB.")

    def wait_sideload(self) -> None:
        info(self, "Sideload", "On the phone, in recovery: Apply update, then Apply from ADB. deADBoot waits up to "
                               "five minutes.")
        self.job(lambda w: self.app.ops(w).wait_for("sideload", timeout=300),
                 lambda ok: info(self, "Sideload", "The phone is waiting for a sideload." if ok else
                                 "The phone did not reach sideload mode."))

    def sideload_rom(self) -> None:
        if self.pkg is None:
            return
        path = str(self.pkg.path)
        if not self.guard(f"Sideload {self.pkg.path.name}", f"adb sideload {path}", need_package=True,
                          wipes=False):
            return
        done = "Sideload finished."
        if self.dev and (self.dev.slot or self.dev.fastboot_vars.get("current-slot")):
            done += ("\n\nThis is an A/B device: the ROM went onto the other slot. Before sideloading add-ons, "
                     "choose Advanced, Reboot to recovery on the phone, so the add-ons land on the new slot. "
                     "Then Apply update, Apply from ADB.")
        self.run_ops(lambda o: o.sideload(path), done, self.record_rom)

    def add_addon(self) -> None:
        p = pick_file(self, "Add-on zip", "Zip (*.zip)")
        if p:
            self.addon_list.addItem(p)

    def sideload_addons(self) -> None:
        zips = [self.addon_list.item(i).text() for i in range(self.addon_list.count())]
        if not zips:
            return
        if not self.guard("Sideload add-ons in this order:\n" + "\n".join(Path(z).name for z in zips),
                          "Between add-ons the phone has to be back in Apply from ADB.\n"
                          "A/B devices: if you just sideloaded the ROM, reboot to recovery first (Advanced, "
                          "Reboot to recovery) so the add-ons go onto the newly installed slot."):
            return

        def fn(o):
            last = None
            for i, z in enumerate(zips):
                if i:
                    o.status("Waiting for the phone to be back in Apply from ADB")
                    if not o.wait_for("sideload", timeout=300):
                        raise RuntimeError("The phone did not return to sideload mode")
                last = o.sideload(z, accept_unsigned=True)
                if not last.ok:
                    return last
            return last

        self.run_ops(fn, "Add-ons sideloaded.")

    def flash_mapped(self) -> None:
        jobs = []
        for r in range(self.map_table.rowCount()):
            img = self.map_table.item(r, 0).text()
            part = self.map_table.cellWidget(r, 1).currentText().strip()
            if not part:
                continue
            both = self.map_table.cellWidget(r, 2).isChecked()
            verity = self.map_table.cellWidget(r, 3).isChecked()
            jobs.append((img, part, both, verity))
        if not jobs:
            info(self, "Flash", "Map at least one image to a partition.")
            return
        src = self.pkg.path if self.pkg else None

        def plan(o):
            for img, part, both, verity in jobs:
                o.flash(part, img, "all" if both else "active", verity)

        if not self.guard("Flash mapped images", self.plan_text(plan), need_package=True):
            return

        def fn(o):
            tmp = hostplatform.cache_dir() / "loose"
            last = None
            for img, part, both, verity in jobs:
                path = img
                if src and src.is_file() and src.suffix.lower() == ".zip":
                    path = str(packages.extract_member(src, img, tmp / Path(img).name))
                last = o.flash(part, path, "all" if both else "active", verity)
                if not last.ok:
                    return last
            return last

        self.run_ops(fn, "Images flashed.", lambda: self.state().record_partial_flash(
            ", ".join(p for _i, p, _b, _v in jobs)))

    # ================================================================ bootloader

    def unlock(self) -> None:
        dev = self.dev
        if dev.fastboot_vars.get("unlock_ability") == "0":
            warn(self, "Can't unlock", "The bootloader reports unlock_ability 0. Turn on OEM unlocking in Developer "
                                      "options (needs a connection on Pixels). If it is greyed out, this device "
                                      "can't be unlocked from the computer side.")
            return
        if not ask(self, "Your data", "Unlocking wipes the phone. Have you pulled the data you want to keep?\n\n"
                                      "Choose No to open the Files tab's rescue pull first."):
            self.app.window.show_tab(self.app.window.tabs_by_name["Files"])
            return
        code = self.code_edit.text().strip()
        if not self.guard("Unlock the bootloader", self.plan_text(lambda o: o.unlock(code)), wipes=True):
            return
        info(self, "Confirm on the phone", "Use the volume keys and power button on the phone to confirm the unlock.")
        self.run_ops(lambda o: o.unlock(code), "Unlock command sent. Confirm on the phone if it is asking.")

    def relock(self) -> None:
        allowed, why = self.state().relock_allowed()
        if not allowed:
            warn(self, "Relock refused", "NEVER RELOCK THE BOOTLOADER UNLESS THE INSTALLED ROM SUPPORTS IT.\n\n" + why)
            return
        if not self.guard("Relock the bootloader", "fastboot flashing lock\n\n" + why,
                          wipes=True):
            return
        self.run_ops(lambda o: o.lock(), "Lock command sent. Confirm on the phone with the volume keys and power.")

    def flash_single(self) -> None:
        part = self.part_combo.currentText().strip()
        img = pick_file(self, f"Image for {part}", "Images (*.img);;All files (*)")
        if not part or not img:
            return
        m = packages.match_device(packages.detect(Path(img)), self.codename())
        if m.blocked:
            warn(self, "Blocked", m.message)
            return
        both = self.single_both.isChecked()
        if not self.guard(f"Flash {Path(img).name} to {part}",
                          self.plan_text(lambda o: o.flash(part, img, "all" if both else "active")),
                          extra=[danger_lines_extra(m)]):
            return
        self.run_ops(lambda o: o.flash(part, img, "all" if both else "active"), f"{part} flashed.",
                     lambda: self.state().record_partial_flash(part))

    def boot_any(self) -> None:
        img = pick_file(self, "Image to boot once", "Images (*.img);;All files (*)")
        if img and self.guard(f"Boot {Path(img).name} once without flashing", f"fastboot boot {img}"):
            self.run_ops(lambda o: o.boot_image(img), "Booted.")

    def set_slot(self, slot: str) -> None:
        if self.guard(f"Set the active slot to {slot}", f"fastboot --set-active={slot}"):
            self.run_ops(lambda o: o.set_active_slot(slot), f"Active slot is now {slot}.")

    # ================================================================ root

    def root_check_profile(self) -> bool:
        installed = self.state().installed_profile
        prof = self.app.profiles.get(installed) or self.cur_profile()
        if prof.root_supported:
            return True
        return typed_confirm(self, self.dev, f"Root a device running {prof.name}",
                             danger_lines=[f"{prof.name} doesn't support root. Rooting means leaving the bootloader "
                                           "unlocked and gives up that ROM's security model. Once a patched image "
                                           "is flashed, relock is disabled for this device."])

    def root_read_build(self) -> None:
        def done(props):
            self.build = magisk.BuildInfo.from_props(props)
            b = self.build
            saved = magisk.stock_path(self.dev.serial, b.build_id, "init_boot").parent
            note = " A stock image for this build is already saved." if any(saved.glob("stock-*.img")) else \
                " No stock image saved for this build yet."
            self.root_status.setText(f"Build {b.build_id} ({b.fingerprint}), slot {b.slot or 'none'}, codename "
                                     f"{b.codename}.{note}")
        self.job(lambda w: self.app.ops(w).getprop(), done)

    def root_partition(self) -> None:
        if not self.build:
            info(self, "Root", "Read the build first (step 1).")
            return
        pkg_path = self.root_pkg.text().strip()

        def work(w):
            o = self.app.ops(w)
            names = o.partition_names()
            if names is not None:
                # The partition list was readable over adb: no trip to the bootloader needed.
                var = "yes" if any(n.startswith("init_boot") for n in names) else "no"
                o.status("Read the partition list over adb: init_boot " + ("present" if var == "yes" else "absent"))
            else:
                r = o.reboot("bootloader")
                if not r.ok:
                    raise RuntimeError(r.err or "could not reach the bootloader")
                var = o.getvar("has-slot:init_boot")
                if var is None:
                    # Non-A/B devices: probe the partition itself.
                    var = "yes" if o.getvar("partition-size:init_boot") else "no"
                o.reboot("system")
            pk = packages.detect(Path(pkg_path)) if pkg_path else None
            has = pk.has_init_boot if pk and pk.kind == packages.FACTORY else None
            return magisk.choose_partition(var, has)

        def done(res):
            part, why = res
            self.root_part = part
            self.root_status.setText(why)
            if not part:
                warn(self, "Root", why)

        self.job(work, done)

    def root_stock(self) -> None:
        if not self.build or not self.root_part:
            info(self, "Root", "Do steps 1 and 2 first.")
            return
        b, part = self.build, self.root_part
        serial = self.dev.serial
        stock = magisk.stock_path(serial, b.build_id, part)
        src_dev = self.src_device.isChecked()
        src_factory = self.src_factory.isChecked()
        pkg_path = self.root_pkg.text().strip()

        def work(w):
            o = self.app.ops(w)
            stock.parent.mkdir(parents=True, exist_ok=True)
            if src_dev:
                if not o.root_available():
                    raise RuntimeError("Root was not granted, so the partition can't be read off the phone.")
                r = o.read_partition_as_root(part, b.slot_suffix, str(stock))
                if not r.ok:
                    raise RuntimeError(r.text)
                return f"Read {part}{b.slot_suffix} straight off the phone. Exact by definition."
            if not pkg_path:
                raise RuntimeError("Choose the package for the running build.")
            pk = packages.detect(Path(pkg_path))
            if src_factory:
                if pk.kind != packages.FACTORY:
                    raise RuntimeError("That is not a factory image.")
                if not packages.build_matches(pk.build, b.build_id, b.incremental):
                    raise RuntimeError(f"The factory image is build {pk.build.upper()}; the phone runs "
                                       f"{b.build_id}. Download the factory image for {b.build_id}.")
                packages.extract_from_factory(pk, part, stock)
                return f"Extracted {part}.img from the factory image for {b.build_id}."
            fp = pk.metadata.get("post-build", "")
            if fp and fp != b.fingerprint:
                raise RuntimeError("The ROM zip's post-build fingerprint doesn't match the running build. Use the "
                                   "zip that is installed.")
            if not self.app.tools.available("payload-dumper"):
                raise RuntimeError("payload-dumper-go is needed: " + self.app.tools.hint("payload-dumper-go"))
            out = stock.parent / "payload"
            r = self.app.runner.run_blocking(Command("payload-dumper", ["-p", part, "-o", str(out), pkg_path]))
            if not r.ok or not (out / f"{part}.img").is_file():
                raise RuntimeError("payload-dumper did not produce the image")
            (out / f"{part}.img").replace(stock)
            shutil.rmtree(out, ignore_errors=True)
            return f"Extracted {part}.img from payload.bin."

        self.job(work, lambda msg: self.root_status.setText(msg + f" Saved as {stock}."))

    def root_push(self) -> None:
        if not self.build or not self.root_part:
            info(self, "Root", "Do steps 1 to 3 first.")
            return
        if not self.root_check_profile():
            return
        stock = magisk.stock_path(self.dev.serial, self.build.build_id, self.root_part)
        if not stock.is_file():
            info(self, "Root", "Get the stock image first (step 3).")
            return

        def work(w):
            o = self.app.ops(w)
            if not o.package_installed("com.topjohnwu.magisk"):
                fetched = downloads.fetch_source("magisk", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
                self.app.runner.line.emit(f"# Magisk {fetched.version}: {fetched.detail}")
                r = o.install([str(fetched.path)], reinstall=True)
                if not r.ok:
                    raise RuntimeError(r.text)
            return o.push(str(stock), "/sdcard/Download/")

        self.job(work, lambda r: self.root_status.setText(
            "Stock image is in /sdcard/Download. In the Magisk app: Install, Select and patch a file, pick it. "
            "Then step 5." if r.ok else "Push failed: " + r.text))

    def root_pull(self) -> None:
        if not self.build or not self.root_part:
            return
        serial, b, part = self.dev.serial, self.build, self.root_part
        stock = magisk.stock_path(serial, b.build_id, part)
        patched = magisk.patched_path(serial, b.build_id, part)

        def work(w):
            o = self.app.ops(w)
            name = magisk.newest_patched(o.list_download_dir())
            if not name:
                raise RuntimeError("No magisk_patched-*.img in /sdcard/Download yet. Patch in the Magisk app first.")
            r = o.pull(f"/sdcard/Download/{name}", str(patched))
            if not r.ok:
                raise RuntimeError(r.text)
            return magisk.check_patched(patched, stock)

        def done(chk):
            self.root_status.setText(chk.message + (f" Saved as {patched}." if chk.ok else ""))
            if not chk.ok:
                warn(self, "Patched image rejected", chk.message)

        self.job(work, done)

    def _patched(self) -> Path | None:
        if not self.build or not self.root_part:
            info(self, "Root", "Do steps 1 to 5 first.")
            return None
        p = magisk.patched_path(self.dev.serial, self.build.build_id, self.root_part)
        if not p.is_file() or not magisk.check_patched(p, magisk.stock_path(self.dev.serial, self.build.build_id,
                                                                          self.root_part)).ok:
            warn(self, "Root", "No checked patched image for this build.")
            return None
        return p

    def root_test_boot(self) -> None:
        p = self._patched()
        if not p:
            return
        if self.root_part == "init_boot":
            warn(self, "No test boot", "init_boot devices have no equivalent of a test boot, so the patched image "
                                       "goes straight to a flash. The saved stock image is your way back.")
            return
        if self.guard("Test boot the patched boot image once (nothing is written)", f"fastboot boot {p}"):
            self.run_ops(lambda o: o.boot_image(str(p)), "Booted. If the phone comes up rooted, flash it for real.")

    def root_flash(self) -> None:
        p = self._patched()
        if not p or not self.root_check_profile():
            return
        part = self.root_part
        if not self.guard(f"Flash the Magisk-patched {part} to the active slot",
                          self.plan_text(lambda o: o.flash(part, str(p), "active")) +
                          "\n\nRelock is disabled for this device after this."):
            return
        serial = self.dev.serial

        def after():
            self.state().record_magisk_flash()
            magisk.cleanup_old_builds(serial)

        self.run_ops(lambda o: o.flash(part, str(p), "active"), "Patched image flashed. Reboot to system.", after)

    def root_restore(self) -> None:
        found = magisk.latest_saved_stock(self.dev.serial)
        if self.build and self.root_part:
            exact = magisk.stock_path(self.dev.serial, self.build.build_id, self.root_part)
            if exact.is_file():
                found = (exact, self.root_part)
        if not found:
            warn(self, "Restore", "No saved stock image for this device.")
            return
        stock, part = found
        if not self.guard(f"Flash the saved stock {part} back ({stock.parent.name})",
                          self.plan_text(lambda o: o.flash(part, str(stock), "active"))):
            return
        self.run_ops(lambda o: o.flash(part, str(stock), "active"), "Stock image restored. Reboot to system.")

    # ================================================================ samsung

    def heimdall(self, args: list[str], writes: bool) -> None:
        def done(r):
            self.h_lab.setText(r.text[-3000:] or f"exit {r.code}")
            if "print-pit" in args:
                show_text(self, "PIT", r.text)
        self.job(lambda _w: self.app.runner.run_blocking(Command("heimdall", args, writes=writes)), done)

    def extract_tar_md5(self) -> None:
        src = pick_file(self, "Samsung firmware", "Odin firmware (*.tar.md5 *.tar);;All files (*)")
        if not src:
            return
        dest = QFileDialog.getExistingDirectory(self, "Extract into")
        if not dest:
            return

        def work(_w):
            with tarfile.open(src) as tf:
                try:
                    tf.extractall(dest, filter="data")
                except tarfile.ReadError:
                    pass  # the trailing md5 line after the archive is expected
            lz4 = sorted(Path(dest).glob("*.lz4"))
            if lz4 and self.app.tools.available("lz4"):
                for f in lz4:
                    self.app.runner.run_blocking(Command("lz4", ["-d", "-f", str(f), str(f.with_suffix(""))]))
            return [p.name for p in Path(dest).iterdir()], bool(lz4) and not self.app.tools.available("lz4")

        def done(res):
            names, need_lz4 = res
            msg = "Extracted: " + ", ".join(sorted(names))
            if need_lz4:
                msg += "\nThe images are lz4-compressed and lz4 isn't installed. Install lz4 and run "\
                       "lz4 -d on each, or extract again."
            self.h_lab.setText(msg)

        self.job(work, done)

    def heimdall_flash(self) -> None:
        part = self.h_part.text().strip()
        if not part:
            info(self, "heimdall", "Enter the PIT partition name first (Print PIT lists them).")
            return
        img = pick_file(self, f"File for {part}", "Images (*.img *.bin);;All files (*)")
        if not img:
            return
        dev = self.dev or _SamsungDummy()
        if not typed_confirm(self, dev, f"heimdall flash --{part} {Path(img).name}",
                             f"heimdall flash --{part} {img}",
                             ["heimdall can't target a serial; make sure only one Samsung phone is in download mode.",
                              "Knox trips permanently once unofficial images are flashed."]):
            return
        self.heimdall(["flash", f"--{part}", img], True)


class _SamsungDummy:
    serial = "download mode"
    confirm_name = "SAMSUNG"


def danger_lines_extra(m: packages.Match) -> str:
    return m.message if m.checked_by != "metadata" else ""
