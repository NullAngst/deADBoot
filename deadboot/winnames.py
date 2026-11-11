"""Windows filename rules for files pulled off Android.

Android allows names Windows refuses. These rules make a pulled tree land
intact on NTFS and record every rename so nothing changes silently.
Only used when hostplatform.rename_pulled_files() is true.
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

BAD_CHARS = re.compile(r'[:*?"<>|\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def sanitize_component(name: str) -> str:
    new = BAD_CHARS.sub("_", name)
    stripped = new.rstrip(" .")
    if stripped != new:
        new = stripped + "_" * (len(new) - len(stripped))
    if not new:
        new = "_"
    stem = new.split(".", 1)[0]
    if stem.upper() in RESERVED:
        new = stem + "_" + new[len(stem):]
    return new


class NameMapper:
    """Maps remote paths (relative, '/'-separated) to safe local relative paths.

    Keeps a per-directory record of lower-cased names so two files that differ
    only by case get a numbered suffix instead of overwriting each other.
    """

    def __init__(self) -> None:
        self._taken: dict[str, dict[str, str]] = {}   # local dir -> {lower name: remote name}
        self._dirs: dict[str, str] = {}               # remote dir -> local dir
        self.renames: list[tuple[str, str]] = []

    def _claim(self, local_dir: str, remote_name: str, safe: str) -> str:
        taken = self._taken.setdefault(local_dir, {})
        candidate = safe
        n = 1
        while candidate.lower() in taken and taken[candidate.lower()] != remote_name:
            stem, dot, ext = safe.rpartition(".")
            if not dot or not stem:
                stem, ext, dot = safe, "", ""
            candidate = f"{stem} ({n}){dot}{ext}"
            n += 1
        taken[candidate.lower()] = remote_name
        return candidate

    def map(self, remote_rel: str) -> str:
        parts = [p for p in remote_rel.split("/") if p]
        local_parts: list[str] = []
        remote_so_far = ""
        for i, part in enumerate(parts):
            remote_so_far = f"{remote_so_far}/{part}" if remote_so_far else part
            is_last = i == len(parts) - 1
            if not is_last and remote_so_far in self._dirs:
                local_parts = self._dirs[remote_so_far].split("/")
                continue
            local_dir = "/".join(local_parts)
            safe = self._claim(local_dir, remote_so_far, sanitize_component(part))
            local_parts.append(safe)
            if not is_last:
                self._dirs[remote_so_far] = "/".join(local_parts)
        local = "/".join(local_parts)
        if local != "/".join(parts):
            self.renames.append((remote_rel, local))
        return local

    def write_report(self, dest: Path, remote_root: str) -> Path | None:
        if not self.renames:
            return None
        report = dest / "renamed-files.txt"
        with report.open("a", encoding="utf-8") as fh:
            for remote, local in self.renames:
                fh.write(f"{remote_root.rstrip('/')}/{remote}\t{local}\n")
        return report


def long_path(path: Path) -> str:
    """Prefix an absolute Windows path with \\\\?\\ so deep trees skip the 260-character limit."""
    p = str(PureWindowsPath(path))
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p
