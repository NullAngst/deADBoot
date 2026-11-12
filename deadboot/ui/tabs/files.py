"""Files tab: remote browser, push/pull queue, one-click rescue pulls."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ... import hostplatform, parsers, winnames
from ...runner import Command, remote_quote
from ..common import Tab, info, pick_dir, pick_files, warn


def human(n: int | None) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(n)


@dataclass
class QueueItem:
    direction: str      # pull or push
    remote: str
    local: str
    size: int | None
    status: str = "queued"


class FilesTab(Tab):
    title = "Files"

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.cwd = "/sdcard"
        self.queue: list[QueueItem] = []
        self.active: int | None = None
        self.running = None
        lay = QVBoxLayout(self)

        nav = QHBoxLayout()
        self.up_btn = self.gate(QPushButton("&Up"), {"device", "recovery"})
        self.path_edit = QLineEdit(self.cwd)
        self.go_btn = self.gate(QPushButton("&Go"), {"device", "recovery"})
        self.refresh_btn = self.gate(QPushButton("&Refresh"), {"device", "recovery"})
        for w in (self.up_btn, self.path_edit, self.go_btn, self.refresh_btn):
            nav.addWidget(w)
        lay.addLayout(nav)

        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Permissions"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        split.addWidget(self.tree)

        qwrap = QWidget()
        ql = QVBoxLayout(qwrap)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.addWidget(QLabel("Transfer queue"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Direction", "Remote", "Local", "Size", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ql.addWidget(self.table)
        self.progress = QProgressBar()
        self.progress.setFormat("%p%")
        ql.addWidget(self.progress)
        split.addWidget(qwrap)
        split.setSizes([400, 220])
        lay.addWidget(split, 1)

        row = QHBoxLayout()
        self.pull_btn = self.gate(QPushButton("P&ull selected..."), {"device", "recovery"})
        self.push_btn = self.gate(QPushButton("Pu&sh files here..."), {"device", "recovery"})
        self.push_dir_btn = self.gate(QPushButton("Push &folder here..."), {"device", "recovery"})
        self.preserve = QCheckBox("Preserve &timestamps (pull -a)")
        self.preserve.setChecked(True)
        for w in (self.pull_btn, self.push_btn, self.push_dir_btn, self.preserve):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Rescue:"))
        self.rescue_all = self.gate(QPushButton("Pull &all of /sdcard..."), {"device", "recovery"})
        self.rescue_media = self.gate(QPushButton("Pull &DCIM + Pictures..."), {"device", "recovery"})
        self.retry_btn = QPushButton("Retry &failed")
        self.stop_btn = QPushButton("Sto&p queue")
        self.clear_btn = QPushButton("Clear &done")
        for w in (self.rescue_all, self.rescue_media, self.retry_btn, self.stop_btn, self.clear_btn):
            row2.addWidget(w)
        row2.addStretch(1)
        lay.addLayout(row2)
        note = QLabel("adb pull does not resume a partial file. If the cable drops, finished files stay "
                      "finished, the interrupted one restarts, and files already on disk at the same size "
                      "are skipped on retry. You can also drop files on the window to push them here.")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.up_btn.clicked.connect(self.go_up)
        self.go_btn.clicked.connect(lambda: self.list_dir(self.path_edit.text().strip() or "/"))
        self.path_edit.returnPressed.connect(lambda: self.list_dir(self.path_edit.text().strip() or "/"))
        self.refresh_btn.clicked.connect(lambda: self.list_dir(self.cwd))
        self.tree.itemDoubleClicked.connect(self.open_item)
        self.pull_btn.clicked.connect(self.pull_selected)
        self.push_btn.clicked.connect(self.push_files)
        self.push_dir_btn.clicked.connect(self.push_folder)
        self.rescue_all.clicked.connect(lambda: self.rescue(["/sdcard"]))
        self.rescue_media.clicked.connect(lambda: self.rescue(["/sdcard/DCIM", "/sdcard/Pictures"]))
        self.retry_btn.clicked.connect(self.retry)
        self.stop_btn.clicked.connect(self.stop)
        self.clear_btn.clicked.connect(self.clear_done)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    # ------------------------------------------------------------ browser

    def on_device(self, dev) -> None:
        self.tree.clear()
        if dev and dev.state in ("device", "recovery"):
            self.list_dir(self.cwd)

    def go_up(self) -> None:
        self.list_dir(posixpath.dirname(self.cwd.rstrip("/")) or "/")

    def open_item(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            self.list_dir(posixpath.join(self.cwd, item.text(0)))

    def list_dir(self, path: str) -> None:
        if not self.dev:
            return
        serial = self.dev.serial
        listing = path.rstrip("/") + "/"

        def work(_w):
            return self.app.runner.run_blocking(
                Command("adb", ["shell", remote_quote("ls", "-la", listing)], serial, timeout_s=30), quiet=True)

        def done(r):
            if not r.ok and not r.out:
                warn(self, "Listing failed", r.text or f"Could not list {path}")
                return
            self.cwd = path if path == "/" else path.rstrip("/")
            self.path_edit.setText(self.cwd)
            self.tree.clear()
            entries = sorted(parsers.parse_ls_la(r.out), key=lambda e: (not e.is_dir, e.name.lower()))
            for e in entries:
                it = QTreeWidgetItem([e.name + ("/" if e.is_dir else ""), "" if e.is_dir else human(e.size),
                                      e.mtime, e.perms])
                it.setText(0, e.name)
                it.setData(0, Qt.ItemDataRole.UserRole, e.size)
                it.setData(0, Qt.ItemDataRole.UserRole + 1, e.is_dir or e.perms.startswith("l"))
                if e.is_dir:
                    it.setText(1, "folder")
                self.tree.addTopLevelItem(it)

        self.job(work, done)

    # ------------------------------------------------------------ queue building

    def pull_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            info(self, "Pull", "Select files or folders in the browser first.")
            return
        dest = pick_dir(self, "Pull into which folder?")
        if dest:
            self.expand_and_queue([posixpath.join(self.cwd, i.text(0)) for i in items], Path(dest))

    def rescue(self, roots: list[str]) -> None:
        dest = pick_dir(self, "Rescue pull: save into which folder?")
        if dest:
            self.expand_and_queue(roots, Path(dest))

    def expand_and_queue(self, remotes: list[str], dest: Path) -> None:
        serial = self.dev.serial

        def work(_w):
            files: list[tuple[str, int | None, str]] = []   # remote, size, root
            for rem in remotes:
                q = remote_quote("find", rem.rstrip("/") + "/", "-type", "f", "-exec", "stat", "-c", "%s %Y %n",
                                 "{}", "+")
                r = self.app.runner.run_blocking(Command("adb", ["shell", q + " 2>/dev/null"], serial,
                                                         timeout_s=600), quiet=True)
                rows = parsers.parse_find_stat(r.out)
                if rows:
                    base = posixpath.dirname(rem.rstrip("/")) or "/"
                    files += [(path, size, base) for size, _mt, path in rows]
                else:
                    r = self.app.runner.run_blocking(Command(
                        "adb", ["shell", remote_quote("stat", "-c", "%s %Y %n", rem)], serial, timeout_s=30), quiet=True)
                    st = parsers.parse_find_stat(r.out)
                    files.append((rem, st[0][0] if st else None, posixpath.dirname(rem) or "/"))
            return files

        def done(files):
            mapper = winnames.NameMapper() if hostplatform.rename_pulled_files() else None
            for remote, size, base in files:
                rel = posixpath.relpath(remote, base)
                if mapper:
                    rel = mapper.map(rel)
                local = dest / Path(*rel.split("/"))
                self.queue.append(QueueItem("pull", remote, str(local), size))
            if mapper:
                mapper.write_report(dest, "/")
                if mapper.renames:
                    info(self, "Renamed for Windows", f"{len(mapper.renames)} file names were changed so Windows "
                         f"accepts them. The list is in {dest / 'renamed-files.txt'}.")
            self.render()
            self.run_queue()

        self.job(work, done)

    def push_files(self, paths: list[str] | None = None) -> None:
        paths = paths or pick_files(self, "Files to push")
        for p in paths:
            self.queue.append(QueueItem("push", self.cwd.rstrip("/") + "/", p, Path(p).stat().st_size
                                        if Path(p).is_file() else None))
        if paths:
            self.render()
            self.run_queue()

    def push_folder(self) -> None:
        d = pick_dir(self, "Folder to push")
        if d:
            self.queue.append(QueueItem("push", self.cwd.rstrip("/") + "/", d, None))
            self.render()
            self.run_queue()

    def drop_files(self, paths: list[str]) -> None:
        if self.dev and self.dev.state in ("device", "recovery"):
            self.push_files(paths)

    # ------------------------------------------------------------ running

    def run_queue(self) -> None:
        if self.running and self.running.running():
            return
        if not self.dev:
            return
        serial = self.dev.serial
        preserve = self.preserve.isChecked()
        queue = self.queue

        def work(w):
            for idx, item in enumerate(queue):
                if w.cancel.is_set():
                    break
                if item.status not in ("queued", "failed"):
                    continue
                w.progress.emit((idx, "running"))
                if item.direction == "pull":
                    local = Path(item.local)
                    if item.size is not None and local.is_file() and local.stat().st_size == item.size:
                        w.progress.emit((idx, "skipped (already there)"))
                        continue
                    local.parent.mkdir(parents=True, exist_ok=True)
                    target = winnames.long_path(local) if hostplatform.IS_WINDOWS and len(str(local)) > 240 \
                        else str(local)
                    args = ["pull"] + (["-a"] if preserve else []) + [item.remote, target]
                else:
                    args = ["push", item.local, item.remote]
                r = self.app.runner.run_blocking(Command("adb", args, serial, writes=item.direction == "push"),
                                                 cancel=w.cancel)
                w.progress.emit((idx, "done" if r.ok else "failed"))
            return None

        def progress(ev):
            idx, status = ev
            self.queue[idx].status = status
            self.active = idx if status == "running" else None
            self.render_row(idx)

        self.running = self.job(work, lambda _r: self.queue_finished(), progress)

    def queue_finished(self) -> None:
        self.active = None
        failed = sum(1 for q in self.queue if q.status == "failed")
        done = sum(1 for q in self.queue if q.status in ("done", "skipped (already there)"))
        self.progress.setValue(100 if not failed else self.progress.value())
        self.app.runner.line.emit(f"# Queue finished: {done} done, {failed} failed")
        if failed:
            warn(self, "Transfer", f"{failed} transfer(s) failed. Fix the connection, then press Retry failed.")

    def retry(self) -> None:
        for q in self.queue:
            if q.status in ("failed", "running"):
                q.status = "queued"
        self.render()
        self.run_queue()

    def stop(self) -> None:
        if self.running:
            self.running.stop()

    def clear_done(self) -> None:
        self.queue[:] = [q for q in self.queue if q.status not in ("done", "skipped (already there)")]
        self.render()

    def render(self) -> None:
        self.table.setRowCount(len(self.queue))
        for i in range(len(self.queue)):
            self.render_row(i)

    def render_row(self, i: int) -> None:
        q = self.queue[i]
        for col, text in enumerate((q.direction, q.remote, q.local, human(q.size), q.status)):
            self.table.setItem(i, col, QTableWidgetItem(text))

    def tick(self) -> None:
        if self.active is None or self.active >= len(self.queue):
            return
        q = self.queue[self.active]
        if q.direction == "pull" and q.size:
            try:
                have = Path(q.local).stat().st_size
            except OSError:
                have = 0
            self.progress.setValue(min(100, int(have * 100 / q.size)))
        else:
            self.progress.setRange(0, 0)
            QTimer.singleShot(400, lambda: self.progress.setRange(0, 100))
