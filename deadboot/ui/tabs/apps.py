"""Apps tab: installed packages, APK install with failure decoding, quick installs, and Debloat."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListWidget, QPushButton, QTableWidget, QTableWidgetItem,
                             QTabWidget, QVBoxLayout, QWidget)

from ... import debloat, downloads, hostplatform, parsers
from ...devicestate import DeviceState
from ...ops import SHIZUKU_PKG
from ..common import Tab, ask, confirm_device, info, pick_dir, pick_files, typed_confirm, warn

ADB_MODES = {"device"}


def unpack_bundle(path: Path, tmp: Path) -> tuple[list[str], list[Path]]:
    """.apks / .xapk bundles are zips of split APKs (xapk may carry OBB data). Returns (apks, obb files)."""
    with zipfile.ZipFile(path) as zf:
        zf.extractall(tmp)
    apks = sorted(str(p) for p in tmp.rglob("*.apk"))
    obbs = sorted(tmp.rglob("*.obb"))
    return apks, obbs


class InstalledPage(QWidget):
    def __init__(self, tab: "AppsTab"):
        super().__init__()
        self.tab = tab
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by package name")
        self.system = QCheckBox("Include &system apps")
        self.refresh_btn = tab.gate(QPushButton("&Refresh"), ADB_MODES)
        row.addWidget(self.filter, 1)
        row.addWidget(self.system)
        row.addWidget(self.refresh_btn)
        lay.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Package", "Version code", "Installed by", "Type", "State"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        lay.addWidget(self.table, 1)
        act = QHBoxLayout()
        self.keep = QCheckBox("Keep &data when uninstalling")
        self.keep.setChecked(True)
        self.uninstall_btn = tab.gate(QPushButton("&Uninstall"), ADB_MODES)
        self.pull_btn = tab.gate(QPushButton("&Pull APK(s)..."), ADB_MODES)
        self.enable_btn = tab.gate(QPushButton("&Enable"), ADB_MODES)
        self.disable_btn = tab.gate(QPushButton("D&isable"), ADB_MODES)
        for w in (self.keep, self.uninstall_btn, self.pull_btn, self.enable_btn, self.disable_btn):
            act.addWidget(w)
        act.addStretch(1)
        lay.addLayout(act)
        self.rows: list[parsers.PackageEntry] = []
        self.refresh_btn.clicked.connect(self.refresh)
        self.filter.textChanged.connect(self.render)
        self.system.toggled.connect(self.refresh)
        self.uninstall_btn.clicked.connect(self.uninstall)
        self.pull_btn.clicked.connect(self.pull)
        self.enable_btn.clicked.connect(lambda: self.set_enabled(True))
        self.disable_btn.clicked.connect(lambda: self.set_enabled(False))

    def selected(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).text() for r in rows]

    def refresh(self) -> None:
        dev = self.tab.dev
        if not dev or not dev.adb_ready:
            return
        include_system = self.system.isChecked()

        def work(_w):
            ops = self.tab.app.ops()
            out = parsers.parse_pm_list(ops.qshell("pm list packages -3 -i --show-versioncode"))
            if include_system:
                out += parsers.parse_pm_list(ops.qshell("pm list packages -s -i --show-versioncode"), system=True)
            disabled = parsers.parse_package_names(ops.qshell("pm list packages -d"))
            for p in out:
                p.enabled = p.name not in disabled
            return out

        def done(rows):
            self.rows = rows
            self.render()

        self.tab.job(work, done)

    def render(self) -> None:
        f = self.filter.text().strip().lower()
        rows = [r for r in self.rows if f in r.name.lower()]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c, t in enumerate((r.name, r.version_code, r.installer or "-", "system" if r.system else "user",
                                   "enabled" if r.enabled else "disabled")):
                self.table.setItem(i, c, QTableWidgetItem(t))
        self.table.setSortingEnabled(True)

    def uninstall(self) -> None:
        pkgs = self.selected()
        dev = self.tab.dev
        if not pkgs:
            return
        keep = self.keep.isChecked()
        listing = "\n".join(pkgs)
        if keep:
            if not confirm_device(self, dev, f"Uninstall, keeping data:\n{listing}"):
                return
        elif not typed_confirm(self, dev, "Uninstall and delete app data", listing,
                               ["The apps' data is deleted with them."]):
            return

        def work(_w):
            ops = self.tab.app.ops()
            return [(p, ops.uninstall(p, keep)) for p in pkgs]

        self.tab.job(work, lambda res: (self.report(res), self.refresh()))

    def report(self, res) -> None:
        bad = [p for p, r in res if not r.ok or "Failure" in r.text]
        if bad:
            warn(self, "Some failed", "\n".join(bad))

    def set_enabled(self, enable: bool) -> None:
        pkgs = self.selected()
        if not pkgs or not confirm_device(self, self.tab.dev, ("Enable" if enable else "Disable") + ":\n" +
                                          "\n".join(pkgs)):
            return

        def work(_w):
            ops = self.tab.app.ops()
            verb = ["pm", "enable", "--user", "0"] if enable else ["pm", "disable-user", "--user", "0"]
            return [(p, ops.adb(["shell"] + verb + [p], writes=True)) for p in pkgs]

        self.tab.job(work, lambda res: (self.report(res), self.refresh()))

    def pull(self) -> None:
        pkgs = self.selected()
        if not pkgs:
            return
        dest = pick_dir(self, "Save APKs into which folder? (one folder per package)")
        if not dest:
            return

        def work(_w):
            ops = self.tab.app.ops()
            done = []
            for p in pkgs:
                paths = ops.apk_paths(p)
                target = Path(dest) / p
                target.mkdir(parents=True, exist_ok=True)
                for rp in paths:
                    ops.pull(rp, str(target / Path(rp).name))
                done.append((p, len(paths)))
            return done

        self.tab.job(work, lambda d: info(self, "Pulled", "\n".join(f"{p}: {n} APK file(s)" for p, n in d)))


class InstallPage(QWidget):
    def __init__(self, tab: "AppsTab"):
        super().__init__()
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("adb install uses the shell install path: no 'install unknown apps' toggle, "
                             "Play Protect left on, and exempt from Google's developer verification as of this "
                             "build. Pick one APK, several split APKs of one app, or an .apks / .xapk bundle."))
        row = QHBoxLayout()
        self.add_btn = QPushButton("&Add APK or bundle...")
        self.clear_btn = QPushButton("C&lear")
        row.addWidget(self.add_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.files = QListWidget()
        lay.addWidget(self.files, 1)
        opts = QHBoxLayout()
        self.r = QCheckBox("Reinstall keeping data (-r)")
        self.r.setChecked(True)
        self.d = QCheckBox("Allow downgrade (-d)")
        self.g = QCheckBox("Grant all runtime permissions (-g)")
        self.b = QCheckBox("Bypass low-target-SDK block (Android 14+)")
        for w in (self.r, self.d, self.g, self.b):
            opts.addWidget(w)
        opts.addStretch(1)
        lay.addLayout(opts)
        self.install_btn = tab.gate(QPushButton("&Install"), ADB_MODES)
        lay.addWidget(self.install_btn)

        lay.addWidget(QLabel("<b>Quick installs</b> (current release, hash checked where the project publishes one)"))
        q = QHBoxLayout()
        self.quick: dict[str, QPushButton] = {}
        for key, label in (("shizuku", "Shi&zuku"), ("droidify", "Droid-&ify"), ("obtainium", "&Obtainium"),
                           ("installwithoptions", "Install &With Options"), ("magisk", "&Magisk")):
            b = tab.gate(QPushButton(label), ADB_MODES)
            b.clicked.connect(lambda _c=False, k=key: self.quick_install(k))
            self.quick[key] = b
            q.addWidget(b)
        self.shizuku_start = tab.gate(QPushButton("Run Shizuku &activation"), ADB_MODES)
        self.shizuku_start.clicked.connect(lambda: tab.app.window.tabs_by_name["F-Droid Setup"].start_shizuku())
        q.addWidget(self.shizuku_start)
        q.addStretch(1)
        lay.addLayout(q)

        self.add_btn.clicked.connect(self.add)
        self.clear_btn.clicked.connect(self.files.clear)
        self.install_btn.clicked.connect(lambda: self.install())

    def add(self) -> None:
        for p in pick_files(self, "APK files or bundles", "Android packages (*.apk *.apks *.xapk);;All files (*)"):
            self.files.addItem(p)

    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            self.files.addItem(p)

    def install(self, override: dict | None = None) -> None:
        paths = [Path(self.files.item(i).text()) for i in range(self.files.count())]
        if not paths:
            info(self, "Install", "Add an APK first.")
            return
        opts = dict(reinstall=self.r.isChecked(), downgrade=self.d.isChecked(), grant=self.g.isChecked(),
                    bypass_low_sdk=self.b.isChecked())
        opts.update(override or {})
        if not confirm_device(self, self.tab.dev, "Install:\n" + "\n".join(p.name for p in paths)):
            return
        self.run_install(paths, opts)

    def run_install(self, paths: list[Path], opts: dict, uninstall_first: str = "") -> None:
        def work(_w):
            ops = self.tab.app.ops()
            if uninstall_first:
                ops.uninstall(uninstall_first, keep_data=False)
            apks: list[str] = []
            obbs: list[Path] = []
            tmp = Path(tempfile.mkdtemp(prefix="deadboot-bundle-"))
            try:
                for p in paths:
                    if p.suffix.lower() in (".apks", ".xapk"):
                        a, o = unpack_bundle(p, tmp / p.stem)
                        apks += a
                        obbs += o
                    else:
                        apks.append(str(p))
                r = ops.install(apks, **opts)
                pkg_obb = []
                if r.ok and obbs:
                    for obb in obbs:
                        parent = obb.parent.name
                        ops.push(str(obb), f"/sdcard/Android/obb/{parent}/")
                        pkg_obb.append(obb.name)
                return r, pkg_obb
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self.tab.job(work, lambda res: self.after_install(res, paths, opts))

    def after_install(self, res, paths: list[Path], opts: dict) -> None:
        r, obbs = res
        if r.dry:
            info(self, "Dry run", "Dry-run mode: the install command was printed, not run.")
            return
        fail = parsers.decode_install_failure(r.text)
        if r.ok and not fail:
            info(self, "Installed", "Installed." + (f"\nOBB data pushed: {', '.join(obbs)}" if obbs else ""))
            return
        if not fail:
            warn(self, "Install failed", r.text[-1500:])
            return
        if fail.suggestion == "bypass_low_sdk" and not opts.get("bypass_low_sdk"):
            if ask(self, "Old app", fail.sentence + "\n\nRetry with --bypass-low-target-sdk-block?"):
                self.run_install(paths, {**opts, "bypass_low_sdk": True})
            return
        if fail.suggestion == "downgrade" and not opts.get("downgrade"):
            if ask(self, "Older version", fail.sentence + "\n\nRetry with downgrade allowed?"):
                self.run_install(paths, {**opts, "downgrade": True})
            return
        if fail.suggestion == "uninstall_reinstall":
            m = re.search(r"Existing package (\S+)", r.text)
            pkg = m.group(1) if m else ""
            if pkg and typed_confirm(self, self.tab.dev, f"Uninstall {pkg} (and its data), then install",
                                     fail.sentence, ["The installed app's data is deleted."]):
                self.run_install(paths, opts, uninstall_first=pkg)
                return
        warn(self, fail.code, fail.sentence)

    def quick_install(self, key: str) -> None:
        dev = self.tab.dev
        if not confirm_device(self, dev, f"Download the current {key} release and install it"):
            return

        def work(w):
            ops = self.tab.app.ops(w)
            if key == "shizuku" and ops.package_installed(SHIZUKU_PKG):
                return "already", None
            fetched = downloads.fetch_source(key, hostplatform.cache_dir() / "apks", hostplatform.cache_dir(),
                                             ops.abi())
            self.tab.app.runner.line.emit(f"# {key} {fetched.version}: {fetched.detail}")
            return fetched, ops.install([str(fetched.path)], reinstall=True)

        def done(res):
            fetched, r = res
            if fetched == "already":
                info(self, "Shizuku", "Shizuku is already installed.")
                return
            note = "" if fetched.verified_by_project else f"\n\nNote: {fetched.detail}."
            if r.ok:
                info(self, "Installed", f"{key} {fetched.version} installed.{note}")
            else:
                f = parsers.decode_install_failure(r.text)
                warn(self, "Install failed", (f.sentence if f else r.text[-1500:]) + note)

        self.tab.job(work, done)


class DebloatPage(QWidget):
    def __init__(self, tab: "AppsTab"):
        super().__init__()
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Removes carrier and vendor bloat without root. Both methods are reversible without a factory reset. "
            "Removing the wrong vendor service can break a camera, a fingerprint reader or OTA updates: disable "
            "first, live with it a few days, then remove. Packages on the do-not-touch list can't be selected. "
            "adb has no command for app display names, so packages are listed by name."))
        row = QHBoxLayout()
        self.vendor = QComboBox()
        self.vendor.addItems(list(debloat.VENDOR_PREFIXES))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search packages")
        self.load_btn = tab.gate(QPushButton("&Load packages"), ADB_MODES)
        row.addWidget(QLabel("Vendor:"))
        row.addWidget(self.vendor)
        row.addWidget(self.search, 1)
        row.addWidget(self.load_btn)
        lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Package", "State", "Note"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        act = QHBoxLayout()
        self.disable_btn = tab.gate(QPushButton("&Disable selected"), ADB_MODES)
        self.remove_btn = tab.gate(QPushButton("&Remove selected for user 0"), ADB_MODES)
        self.remove_btn.setProperty("danger", True)
        self.restore_btn = tab.gate(QPushButton("Re&store selected"), ADB_MODES)
        self.restore_all_btn = tab.gate(QPushButton("Restore &all recorded"), ADB_MODES)
        self.reapply_btn = tab.gate(QPushButton("Re-a&pply recorded list"), ADB_MODES)
        self.export_btn = tab.gate(QPushButton("&Export list..."), None)
        self.import_btn = tab.gate(QPushButton("&Import list..."), ADB_MODES)
        for w in (self.disable_btn, self.remove_btn, self.restore_btn, self.restore_all_btn, self.reapply_btn,
                  self.export_btn, self.import_btn):
            act.addWidget(w)
        act.addStretch(1)
        lay.addLayout(act)
        self.data: list[tuple[str, str]] = []     # (package, state)
        self.guard: set[str] = set()
        self.load_btn.clicked.connect(self.load)
        self.vendor.currentTextChanged.connect(self.render)
        self.search.textChanged.connect(self.render)
        self.disable_btn.clicked.connect(lambda: self.apply("disable"))
        self.remove_btn.clicked.connect(lambda: self.apply("remove"))
        self.restore_btn.clicked.connect(self.restore_selected)
        self.restore_all_btn.clicked.connect(self.restore_all)
        self.reapply_btn.clicked.connect(self.reapply)
        self.export_btn.clicked.connect(self.export)
        self.import_btn.clicked.connect(self.import_list)

    def state(self) -> DeviceState:
        return DeviceState(self.tab.dev.serial)

    def load(self) -> None:
        def work(_w):
            ops = self.tab.app.ops()
            system = parsers.parse_package_names(ops.qshell("pm list packages -s"))
            disabled = parsers.parse_package_names(ops.qshell("pm list packages -d"))
            every = parsers.parse_package_names(ops.qshell("pm list packages -u"))
            installed = parsers.parse_package_names(ops.qshell("pm list packages"))
            removed = every - installed
            guard = self.tab.app.protected | ops.dynamic_protected()
            rows = []
            for p in sorted(system | removed):
                st = "removed" if p in removed else "disabled" if p in disabled else "enabled"
                rows.append((p, st))
            return rows, guard

        def done(res):
            self.data, self.guard = res
            self.render()

        self.tab.job(work, done)

    def render(self) -> None:
        vendor = self.vendor.currentText()
        q = self.search.text().strip().lower()
        record = self.state().debloat_record() if self.tab.dev else {}
        rows = [(p, s) for p, s in self.data if debloat.matches_vendor(p, vendor) and q in p.lower()]
        self.table.setRowCount(len(rows))
        for i, (p, s) in enumerate(rows):
            it = QTableWidgetItem(p)
            if p in self.guard:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                note = "do-not-touch"
            else:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                note = f"recorded: {record[p]['mode']}" if p in record else ""
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(s))
            self.table.setItem(i, 2, QTableWidgetItem(note))

    def checked(self) -> list[str]:
        out = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                out.append(it.text())
        return out

    def apply(self, mode: str, pkgs: list[str] | None = None) -> None:
        pkgs = pkgs if pkgs is not None else self.checked()
        pkgs = [p for p in pkgs if p not in self.guard]
        if not pkgs:
            info(self, "Debloat", "Tick the packages first.")
            return
        listing = "\n".join(pkgs)
        dev = self.tab.dev
        if mode == "disable":
            if not confirm_device(self, dev, f"Disable {len(pkgs)} package(s) (pm disable-user):\n{listing}"):
                return
        elif not typed_confirm(self, dev, f"Remove {len(pkgs)} package(s) for user 0 (pm uninstall -k --user 0)",
                               listing, ["Reversible with Restore, but a wrong vendor service can break features."]):
            return
        model = dev.model

        def work(_w):
            ops = self.tab.app.ops()
            return ops.debloat(pkgs, mode, self.tab.app.protected, self.state(), model)

        def done(s):
            msg = f"{len(s.done)} {mode}d."
            if s.refused:
                msg += f"\nRefused (do-not-touch): {', '.join(s.refused)}"
            if s.skipped:
                msg += f"\nNot installed: {', '.join(s.skipped)}"
            if s.failed:
                msg += f"\nFailed: {', '.join(s.failed)}"
            info(self, "Debloat", msg)
            self.load()

        self.tab.job(work, done)

    def _restore(self, pkgs: list[str]) -> None:
        st = self.state()
        rec = st.debloat_record()

        def work(_w):
            ops = self.tab.app.ops()
            out = []
            for p in pkgs:
                mode = rec.get(p, {}).get("mode") or ("disable" if dict(self.data).get(p) == "disabled" else "remove")
                r = ops.restore_package(p, mode)
                if r.ok and "Failure" not in r.text:
                    st.forget_debloat(p)
                    out.append(p)
            return out

        self.tab.job(work, lambda done: (info(self, "Restored", f"Restored {len(done)} package(s)."), self.load()))

    def restore_selected(self) -> None:
        pkgs = self.checked()
        if pkgs and confirm_device(self, self.tab.dev, "Restore:\n" + "\n".join(pkgs)):
            self._restore(pkgs)

    def restore_all(self) -> None:
        pkgs = list(self.state().debloat_record())
        if not pkgs:
            info(self, "Restore", "Nothing recorded for this device.")
            return
        if confirm_device(self, self.tab.dev, "Restore every recorded package:\n" + "\n".join(pkgs)):
            self._restore(pkgs)

    def reapply(self) -> None:
        rec = self.state().debloat_record()
        removes = [p for p, v in rec.items() if v["mode"] == "remove"]
        disables = [p for p, v in rec.items() if v["mode"] == "disable"]
        if removes:
            self.apply("remove", removes)
        if disables:
            self.apply("disable", disables)
        if not rec:
            info(self, "Re-apply", "Nothing recorded for this device.")

    def export(self) -> None:
        if not self.tab.dev:
            return
        rec = self.state().debloat_record()
        model = (self.tab.dev.model or "device").replace(" ", "_")
        default = hostplatform.recipes_dir() / "lists" / f"{model}-bloat.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Export removal list", str(default), "Text (*.txt)")
        if path:
            debloat.write_list(Path(path), list(rec), f"Debloat list exported from {self.tab.dev.model}\n"
                                                      "Use it in a recipe's debloat step or import it here.")
            info(self, "Exported", f"{len(rec)} package(s) written to {path}")

    def import_list(self) -> None:
        path = pick_files(self, "Debloat list", "Text (*.txt);;All files (*)")
        if not path:
            return
        wanted = debloat.read_list(Path(path[0]))
        hits = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it and it.text() in wanted and it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                it.setCheckState(Qt.CheckState.Checked)
                hits += 1
        info(self, "Imported", f"Ticked {hits} of {len(wanted)} listed packages. Review, then Disable or Remove. "
                               "Load packages first if the list is empty.")


class AppsTab(Tab):
    title = "Apps"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        lay = QVBoxLayout(self)
        self.sub = QTabWidget()
        self.installed = InstalledPage(self)
        self.install = InstallPage(self)
        self.debloat = DebloatPage(self)
        self.sub.addTab(self.installed, "Installed")
        self.sub.addTab(self.install, "Install")
        self.sub.addTab(self.debloat, "Debloat")
        lay.addWidget(self.sub)

    def on_device(self, dev) -> None:
        self.installed.table.setRowCount(0)
        self.debloat.table.setRowCount(0)
        self.debloat.data = []
        if dev and dev.adb_ready:
            self.installed.refresh()

    def drop_files(self, paths: list[str]) -> None:
        self.install.add_paths(paths)
        self.sub.setCurrentWidget(self.install)
