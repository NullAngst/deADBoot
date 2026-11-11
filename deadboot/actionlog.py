"""Persistent action log: one file per day, every device command with timestamp and serial."""

from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path

from . import hostplatform

_lock = threading.Lock()


class ActionLog:
    def __init__(self, directory: Path | None = None, retention_days: int = 30):
        self.directory = directory or hostplatform.logs_dir()
        self.retention_days = retention_days

    def _file(self, when: _dt.datetime) -> Path:
        return self.directory / f"{when:%Y-%m-%d}.log"

    def write(self, serial: str, command: str, result: str) -> None:
        now = _dt.datetime.now()
        line = f"{now:%Y-%m-%d %H:%M:%S}\t{serial or '-'}\t{command}\t{result}\n"
        with _lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with self._file(now).open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass

    def rotate(self) -> int:
        """Delete day files older than the retention window. Returns how many were removed."""
        if not self.directory.is_dir():
            return 0
        cutoff = _dt.date.today() - _dt.timedelta(days=self.retention_days)
        removed = 0
        for f in self.directory.glob("*.log"):
            try:
                day = _dt.date.fromisoformat(f.stem)
            except ValueError:
                continue
            if day < cutoff:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
