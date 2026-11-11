"""Recipes tab: list, run (inputs, checks, plan, one typed confirmation), edit, duplicate."""

from __future__ import annotations

import html
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ... import downloads, hostplatform, recipes
from ...devicestate import DeviceState
from ...ops import DeviceOps
from ..common import Tab, ask, info, open_path, pick_dir, pick_file, typed_confirm, warn

BOOL_FIELDS = {"accept_unsigned", "disable_verity", "keep_data", "wipe", "reinstall", "downgrade", "grant",
               "bypass_low_sdk", "preserve", "install_with_options"}
CHOICES = {
    "to": list(recipes.REBOOT_TARGETS),
    "until": [""] + sorted(recipes.WAIT_STATES),
    "mode": ["remove", "disable"],
    "slots": ["active", "all"],
    "slot": ["a", "b"],
    "source": [""] + sorted(recipes.NAMED_SOURCES),
    "client": ["droidify", "obtainium"],
    "root": ["auto", "yes", "no"],
}


# ---------------------------------------------------------------- run dialog

class InputsDialog(QDialog):
    """Ask for every input up front, with an optional published hash per input."""

    def __init__(self, parent, recipe: recipes.Recipe):
        super().__init__(parent)
        self.setWindowTitle(f"Inputs: {recipe.name}")
        self.recipe = recipe
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.paths: dict[str, QLineEdit] = {}
        self.hashes: dict[str, QLineEdit] = {}
        for inp in recipe.inputs:
            row = QHBoxLayout()
            edit = QLineEdit()
            btn = QPushButton("Choose...")
            folder = inp.get("kind") == "folder"
            btn.clicked.connect(lambda _c=False, e=edit, f=folder: e.setText(
                (pick_dir(self, "Folder") if f else pick_file(self, "File")) or e.text()))
            row.addWidget(edit, 1)
            row.addWidget(btn)
            label = inp.get("label") or inp["id"]
            if inp.get("optional"):
                label += " (optional)"
            form.addRow(label, row)
            self.paths[inp["id"]] = edit
            if inp.get("kind", "file") in ("file", "package", "apk"):
                h = QLineEdit()
                h.setPlaceholderText("Published SHA-256 (optional, checked before anything runs)")
                form.addRow("", h)
                self.hashes[inp["id"]] = h
        lay.addLayout(form)
        self.low_batt = QCheckBox("Allow a flash below the battery threshold (asks for a typed confirmation)")
        lay.addWidget(self.low_batt)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Check inputs")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(720, 120 + 60 * len(recipe.inputs))

    def values(self) -> tuple[dict[str, str], dict[str, str]]:
        return ({k: e.text().strip() for k, e in self.paths.items()},
                {k: e.text().strip() for k, e in self.hashes.items() if e.text().strip()})


class PlanDialog(QDialog):
    def __init__(self, parent, recipe, plan, check, dev):
        super().__init__(parent)
        self.setWindowTitle(f"Plan: {recipe.name}")
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        out = [f"<h3>{html.escape(recipe.name)}</h3><p>Device: {html.escape(dev.confirm_name)} (serial {dev.serial})</p>"]
        if check is not None:
            for p in check.problems:
                out.append(f"<p style='color:#c62828'><b>Blocked:</b> {html.escape(p)}</p>")
            for w in check.warnings:
                out.append(f"<p><b>Warning:</b> {html.escape(w)}</p>")
            for c in check.passed:
                out.append(f"<p><b>Checked:</b> {html.escape(c)}</p>")
        out.append("<ol>")
        cur = None
        for item in plan:
            if item.step != cur:
                if cur is not None:
                    out.append("</ul></li>")
                cur = item.step
                step = recipe.steps[item.step] if 0 <= item.step < len(recipe.steps) else {"action": "?"}
                marks = any(i.wipes for i in plan if i.step == cur)
                writes = any(i.writes for i in plan if i.step == cur)
                tag = " <b style='color:#c62828'>[WIPES]</b>" if marks else (" <b>[writes]</b>" if writes else "")
                out.append(f"<li><b>{step['action']}</b>{tag}<ul>")
            esc = item.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if item.kind == "cmd":
                out.append(f"<li><code>{esc}</code></li>")
            elif item.kind == "wait":
                out.append(f"<li><i>Wait: {esc}</i></li>")
            else:
                out.append(f"<li>{esc}</li>")
        if cur is not None:
            out.append("</ul></li>")
        out.append("</ol>")
        view.setHtml("".join(out))
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if check is not None and check.ok:
            self.run_btn = bb.addButton("Run this plan...", QDialogButtonBox.ButtonRole.AcceptRole)
            self.run_btn.setProperty("danger", recipe.flashes)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(820, 620)


class WaitBridge(QWidget):
    """Lets the worker thread block on a GUI prompt. The prompt also closes itself when the device arrives."""

    ask_sig = pyqtSignal(str, str)
    close_sig = pyqtSignal()

    def __init__(self, tab: "RecipesTab"):
        super().__init__()
        self.tab = tab
        self.event = threading.Event()
        self.answer = False
        self.box: QMessageBox | None = None
        self.ask_sig.connect(self._show)
        self.close_sig.connect(self._close_box)

    def wait(self, message: str, until: str | None, ops: DeviceOps) -> bool:
        self.event.clear()
        self.answer = False
        self.ask_sig.emit(message, until or "")
        if until:
            while not self.event.is_set():
                if ops.cancel.is_set():
                    self._close(False)
                    return False
                accept = {"fastboot": {"bootloader", "fastbootd"}, "device": {"device"}}.get(until, {until})
                if ops.state() in accept:
                    self._close(True)
                    return True
                ops.cancel.wait(1.5)
            return self.answer
        self.event.wait()
        return self.answer

    def _show(self, message: str, until: str) -> None:
        self.box = QMessageBox(self.tab)
        self.box.setWindowTitle("Recipe waiting")
        extra = f"\n\nContinues by itself when the device is in {until} mode." if until else ""
        self.box.setText(message + extra)
        self.box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        self.box.addButton("Stop recipe", QMessageBox.ButtonRole.RejectRole)
        self.box.setWindowModality(Qt.WindowModality.NonModal)
        self.box.buttonClicked.connect(lambda b: self._answer(self.box.buttonRole(b) ==
                                                              QMessageBox.ButtonRole.AcceptRole))
        self.box.show()

    def _answer(self, ok: bool) -> None:
        self.answer = ok
        self.event.set()

    def _close(self, ok: bool) -> None:
        self.answer = ok
        self.event.set()
        self.close_sig.emit()

    def _close_box(self) -> None:
        if self.box is not None:
            self.box.close()
            self.box = None


class _Callbacks:
    def __init__(self, bridge: WaitBridge, ops: DeviceOps, step_sig):
        self.bridge = bridge
        self.ops = ops
        self.step_sig = step_sig

    def wait(self, message: str, until: str | None) -> bool:
        return self.bridge.wait(message, until, self.ops)

    def step(self, index: int, text: str) -> None:
        self.step_sig(index, text)


# ---------------------------------------------------------------- editor

class StepEditor(QDialog):
    def __init__(self, parent, recipe: recipes.Recipe):
        super().__init__(parent)
        self.setWindowTitle(f"Edit recipe: {recipe.name}")
        self.recipe = recipe
        self.steps = [dict(s) for s in recipe.steps]
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(recipe.name)
        self.desc = QLineEdit(recipe.description)
        self.profile = QComboBox()
        self.profile.addItem("(none)", "")
        for pid, p in sorted(parent.app.profiles.items()):
            self.profile.addItem(p.name, pid)
        self.profile.setCurrentIndex(max(0, self.profile.findData(recipe.rom_profile)))
        self.devices = QLineEdit(", ".join(recipe.devices))
        self.devices.setPlaceholderText("Codenames, comma separated. Empty means any device the files match.")
        self.inputs = QPlainTextEdit("\n".join(
            f"{i['id']} | {i.get('label', '')} | {i.get('kind', 'file')}" for i in recipe.inputs))
        self.inputs.setPlaceholderText("One input per line: id | label | kind (file, package, apk, signature, "
                                       "allowed_signers, folder)")
        self.inputs.setMaximumHeight(90)
        form.addRow("Name", self.name)
        form.addRow("Description", self.desc)
        form.addRow("ROM profile", self.profile)
        form.addRow("Devices", self.devices)
        form.addRow("Inputs", self.inputs)
        lay.addLayout(form)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(self.reorder)
        ll.addWidget(self.list)
        row = QHBoxLayout()
        self.add_action = QComboBox()
        self.add_action.addItems(sorted(recipes.ACTIONS))
        add = QPushButton("&Add step")
        rm = QPushButton("&Remove")
        row.addWidget(self.add_action)
        row.addWidget(add)
        row.addWidget(rm)
        ll.addLayout(row)
        split.addWidget(left)
        self.fields_box = QWidget()
        self.fields = QFormLayout(self.fields_box)
        split.addWidget(self.fields_box)
        split.setSizes([320, 420])
        lay.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        add.clicked.connect(self.add_step)
        rm.clicked.connect(self.remove_step)
        self.list.currentRowChanged.connect(self.show_step)
        self.render()
        self.resize(900, 640)

    def render(self) -> None:
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.steps:
            extra = ", ".join(f"{k}={v}" for k, v in s.items() if k != "action")
            it = QListWidgetItem(f"{s['action']}  {extra}")
            it.setData(Qt.ItemDataRole.UserRole, id(s))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.steps:
            self.list.setCurrentRow(min(max(cur, 0), len(self.steps) - 1))
            self.show_step(self.list.currentRow())

    def reorder(self, *_a) -> None:
        by_id = {id(s): s for s in self.steps}
        self.steps = [by_id[self.list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.list.count())]

    def add_step(self) -> None:
        action = self.add_action.currentText()
        req, _opt, _ = recipes.ACTIONS[action]
        step = {"action": action}
        for k in sorted(req):
            step[k] = CHOICES.get(k, [""])[0] if k in CHOICES else ""
        self.steps.insert(self.list.currentRow() + 1 if self.steps else 0, step)
        self.render()

    def remove_step(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.steps):
            del self.steps[r]
            self.render()

    def show_step(self, row: int) -> None:
        while self.fields.rowCount():
            self.fields.removeRow(0)
        if not 0 <= row < len(self.steps):
            return
        step = self.steps[row]
        req, opt, desc = recipes.ACTIONS[step["action"]]
        self.fields.addRow(QLabel(f"<b>{step['action']}</b>: {desc}"))
        for key in sorted(req) + sorted(opt):
            val = step.get(key)
            if key in BOOL_FIELDS:
                w = QCheckBox()
                w.setChecked(bool(val))
                w.toggled.connect(lambda v, k=key, s=step: self.set_field(s, k, v or None))
            elif key in CHOICES:
                w = QComboBox()
                w.addItems(CHOICES[key])
                w.setCurrentText(str(val or ""))
                w.currentTextChanged.connect(lambda v, k=key, s=step: self.set_field(s, k, v or None))
            else:
                w = QLineEdit(str(val or ""))
                if key in ("image", "file", "package", "list", "local"):
                    w.setPlaceholderText("{input_id}, or a path (relative paths are relative to the recipe)")
                w.textChanged.connect(lambda v, k=key, s=step: self.set_field(s, k, v or None))
            self.fields.addRow(key + (" *" if key in req else ""), w)

    def set_field(self, step: dict, key: str, value) -> None:
        if value is None:
            step.pop(key, None)
        else:
            step[key] = value
        r = self.list.currentRow()
        extra = ", ".join(f"{k}={v}" for k, v in step.items() if k != "action")
        if 0 <= r < self.list.count():
            self.list.item(r).setText(f"{step['action']}  {extra}")

    def save(self) -> None:
        inputs = []
        for line in self.inputs.toPlainText().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if parts and parts[0]:
                inp = {"id": parts[0]}
                if len(parts) > 1 and parts[1]:
                    inp["label"] = parts[1]
                if len(parts) > 2 and parts[2]:
                    inp["kind"] = parts[2]
                inputs.append(inp)
        r = recipes.Recipe(name=self.name.text().strip(), path=self.recipe.path, description=self.desc.text().strip(),
                           rom_profile=self.profile.currentData() or "",
                           devices=[d.strip() for d in self.devices.text().split(",") if d.strip()],
                           inputs=inputs, steps=self.steps)
        try:
            recipes.save(r)
        except (recipes.RecipeError, TypeError) as exc:
            warn(self, "Not saved", str(exc))
            return
        self.recipe = r
        self.accept()


# ---------------------------------------------------------------- tab

class RecipesTab(Tab):
    title = "Recipes"
    step_signal = pyqtSignal(int, str)

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.items: list[recipes.Recipe] = []
        self.paused: dict | None = None      # {"recipe", "inputs", "index", "serial"}
        lay = QVBoxLayout(self)
        split = QSplitter()
        self.list = QListWidget()
        split.addWidget(self.list)
        self.detail = QTextBrowser()
        split.addWidget(self.detail)
        split.setSizes([300, 600])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.run_btn = self.gate(QPushButton("&Run..."), None)
        self.plan_btn = self.gate(QPushButton("Show &plan..."), None)
        self.resume_btn = QPushButton("Res&ume from failed step")
        self.resume_btn.setEnabled(False)
        self.abandon_btn = QPushButton("A&bandon")
        self.abandon_btn.setEnabled(False)
        self.stop_btn = QPushButton("&Stop")
        self.stop_btn.setEnabled(False)
        self.new_btn = QPushButton("&New")
        self.edit_btn = QPushButton("&Edit")
        self.text_btn = QPushButton("Edit as &text")
        self.dup_btn = QPushButton("&Duplicate")
        self.folder_btn = QPushButton("Open &folder")
        self.reload_btn = QPushButton("Re&load")
        for w in (self.run_btn, self.plan_btn, self.resume_btn, self.abandon_btn, self.stop_btn, self.new_btn,
                  self.edit_btn, self.text_btn, self.dup_btn, self.folder_btn, self.reload_btn):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bridge = WaitBridge(self)
        self.handle = None
        self.list.currentRowChanged.connect(self.show_detail)
        self.run_btn.clicked.connect(lambda: self.start(plan_only=False))
        self.plan_btn.clicked.connect(lambda: self.start(plan_only=True))
        self.resume_btn.clicked.connect(self.resume)
        self.abandon_btn.clicked.connect(self.abandon)
        self.stop_btn.clicked.connect(lambda: self.handle and self.handle.stop())
        self.new_btn.clicked.connect(self.new)
        self.edit_btn.clicked.connect(self.edit)
        self.text_btn.clicked.connect(self.edit_text)
        self.dup_btn.clicked.connect(self.duplicate)
        self.folder_btn.clicked.connect(lambda: open_path(hostplatform.recipes_dir()))
        self.reload_btn.clicked.connect(self.reload)
        self.step_signal.connect(lambda i, t: self.status.setText(f"Step {i + 1}: {t}"))
        self.reload()

    def reload(self) -> None:
        self.items, errors = recipes.load_all()
        self.list.clear()
        for r in self.items:
            self.list.addItem(r.name + ("  [flashes]" if r.flashes else ""))
        for e in errors:
            it = QListWidgetItem("ERROR: " + e)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(it)
        if self.items:
            self.list.setCurrentRow(0)

    def current(self) -> recipes.Recipe | None:
        r = self.list.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def show_detail(self, _row: int) -> None:
        r = self.current()
        if not r:
            self.detail.setPlainText(self.list.currentItem().text() if self.list.currentItem() else "")
            return
        e = html.escape
        lines = [f"<h3>{e(r.name)}</h3>", f"<p>{e(r.description)}</p>"]
        if r.rom_profile:
            lines.append(f"<p>ROM profile: {e(r.rom_profile)}</p>")
        if r.inputs:
            lines.append("<p><b>Inputs</b></p><ul>" + "".join(
                f"<li>{e(i.get('label') or i['id'])} <i>({e(i['id'])})</i></li>" for i in r.inputs) + "</ul>")
        lines.append("<p><b>Steps</b></p><ol>" + "".join(
            "<li>" + e(s["action"] + " " + ", ".join(f"{k}={v}" for k, v in s.items() if k != "action")) + "</li>"
            for s in r.steps) + "</ol>")
        lines.append(f"<p><small>{e(str(r.path))}</small></p>")
        self.detail.setHtml("".join(lines))

    # ------------------------------------------------------------ editing

    def new(self) -> None:
        r = recipes.Recipe(name="New recipe", steps=[{"action": "note", "message": "Edit me"}])
        ed = StepEditor(self, r)
        if ed.exec():
            self.reload()

    def edit(self) -> None:
        r = self.current()
        if r and StepEditor(self, r).exec():
            self.reload()

    def edit_text(self) -> None:
        r = self.current()
        if r and r.path:
            open_path(r.path)

    def duplicate(self) -> None:
        r = self.current()
        if not r:
            return
        copy = recipes.Recipe(name=r.name + " (copy)", description=r.description, rom_profile=r.rom_profile,
                              devices=list(r.devices), inputs=[dict(i) for i in r.inputs],
                              steps=[dict(s) for s in r.steps])
        base = r.path.with_name(r.path.stem + "-copy.toml") if r.path else None
        n = 2
        while base and base.exists():
            base = r.path.with_name(f"{r.path.stem}-copy{n}.toml")
            n += 1
        recipes.save(copy, base)
        self.reload()

    # ------------------------------------------------------------ running

    def profile_for(self, r):
        return self.app.profiles.get(r.rom_profile) if r.rom_profile else None

    def verify_sig(self, profile):
        tab = self.app.window.tabs_by_name["Flashing & Root"]

        def fn(target: str, sig: str, signers: str) -> tuple[bool, str]:
            return tab.check_signature(profile, Path(target), Path(sig), Path(signers))
        return fn

    def start(self, plan_only: bool) -> None:
        r = self.current()
        dev = self.dev
        if not r or not dev:
            return
        if r.flashes and not self.app.settings.flashing_acknowledged:
            warn(self, "Flashing", "This recipe flashes. Open the Flashing & Root tab and acknowledge its warning "
                                   "first.")
            return
        inputs, hashes = {}, {}
        low_batt = False
        if r.inputs:
            dlg = InputsDialog(self, r)
            if not dlg.exec():
                return
            inputs, hashes = dlg.values()
            low_batt = dlg.low_batt.isChecked()
        if low_batt and r.flashes and not typed_confirm(self, dev, "Run a flashing recipe below the battery "
                                                                    "threshold", expected="LOW BATTERY"):
            return
        profile = self.profile_for(r)
        protected = set(self.app.protected)
        serial = dev.serial
        start_mode = dev.state

        def work(w):
            ops = DeviceOps(self.app.runner, serial, cancel=w.cancel)
            protected_now = protected | ops.dynamic_protected()
            check = None
            if not plan_only:
                battery = ops.battery() if dev.adb_ready else dev.battery
                codename = dev.codename or (ops.getvar("product") if dev.in_fastboot else "") or ""
                check = recipes.precheck(r, inputs, hashes, ops, profile, codename, battery,
                                         self.app.settings.battery_threshold, ops.lock_state(),
                                         battery_override=low_batt,
                                         verify_signature=self.verify_sig(profile) if profile else None,
                                         fastboot_version=self.app.tools.status("fastboot").version)
            plan = recipes.plan(r, inputs, self.app.runner, serial, protected_now, profile, start_mode)
            return plan, check

        def done(res):
            plan, check = res
            dlg = PlanDialog(self, r, plan, check, dev)
            if not dlg.exec() or plan_only or check is None or not check.ok:
                return
            self.last_verified = set(check.verified)
            if not typed_confirm(self, dev, f"Run recipe '{r.name}' ({len(r.steps)} steps)",
                                 "One confirmation covers the whole plan you just read.",
                                 ["Steps marked WIPES erase the device."] if any(p.wipes for p in plan) else None):
                return
            self.execute(r, inputs, 0, serial, profile)

        self.job(work, done)

    def execute(self, r: recipes.Recipe, inputs: dict, start: int, serial: str, profile) -> None:
        self.stop_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.abandon_btn.setEnabled(False)
        model = self.dev.model if self.dev else ""
        verified = set(getattr(self, "last_verified", set()))

        def work(w):
            ops = DeviceOps(self.app.runner, serial, cancel=w.cancel)
            ctx = recipes.Context(r, inputs, ops, profile, set(self.app.protected), DeviceState(serial), model,
                                  self.app.settings, log=self.app.runner.line.emit)
            ctx.callbacks = _Callbacks(self.bridge, ops, self.step_signal.emit)
            ctx.verified = set(verified)
            for i in range(start, len(r.steps)):
                if w.cancel.is_set():
                    return i, "stopped"
                step = r.steps[i]
                self.step_signal.emit(i, step["action"])
                self.app.runner.line.emit(f"# Recipe step {i + 1}/{len(r.steps)}: {step['action']}")
                try:
                    ok, msg = recipes.run_step(ctx, i, step)
                except (recipes.RecipeError, downloads.DownloadError, RuntimeError, OSError) as exc:
                    ok, msg = False, str(exc)
                if not ok:
                    return i, msg or "failed"
            return None, ""

        def done(res):
            idx, msg = res
            self.stop_btn.setEnabled(False)
            self.handle = None
            if idx is None:
                self.paused = None
                self.status.setText(f"Recipe '{r.name}' finished.")
                info(self, "Recipe", f"'{r.name}' finished.")
                return
            self.paused = {"recipe": r, "inputs": inputs, "index": idx, "serial": serial, "profile": profile}
            self.resume_btn.setEnabled(True)
            self.abandon_btn.setEnabled(True)
            self.status.setText(f"Stopped at step {idx + 1} ({r.steps[idx]['action']}): {msg}")
            warn(self, "Recipe stopped", f"Step {idx + 1} ({r.steps[idx]['action']}) failed:\n\n{msg}\n\nNothing "
                                         "after it ran. Fix the problem, then Resume, or Abandon.")

        self.handle = self.job(work, done)

    def resume(self) -> None:
        p = self.paused
        if not p:
            return
        if not self.dev or self.dev.serial != p["serial"]:
            warn(self, "Resume", "Select the device the recipe was running on.")
            return
        if ask(self, "Resume", f"Resume '{p['recipe'].name}' at step {p['index'] + 1} "
                               f"({p['recipe'].steps[p['index']]['action']})?"):
            self.execute(p["recipe"], p["inputs"], p["index"], p["serial"], p["profile"])

    def abandon(self) -> None:
        self.paused = None
        self.resume_btn.setEnabled(False)
        self.abandon_btn.setEnabled(False)
        self.status.setText("Recipe abandoned.")
