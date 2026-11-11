"""Android backup (.ab) extraction.

The documented format: a text header of four lines (ANDROID BACKUP, version,
compressed flag, encryption), then a zlib stream wrapping a tar archive.
Unencrypted backups are unpacked with Python's own zlib and tarfile. Encrypted
ones go through abe (Android Backup Extractor), which handles the key derivation.
"""

from __future__ import annotations

import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"ANDROID BACKUP"


@dataclass
class AbHeader:
    version: int
    compressed: bool
    encryption: str
    offset: int


class AbError(ValueError):
    pass


def read_header(path: Path) -> AbHeader:
    with path.open("rb") as fh:
        lines = []
        offset = 0
        for _ in range(4):
            line = fh.readline(256)
            offset += len(line)
            lines.append(line.rstrip(b"\n"))
    if lines[0] != MAGIC:
        raise AbError("not an Android backup file")
    try:
        version = int(lines[1])
        compressed = lines[2] == b"1"
    except ValueError as exc:
        raise AbError("damaged backup header") from exc
    return AbHeader(version, compressed, lines[3].decode("ascii", "replace"), offset)


def to_tar(ab: Path, tar_out: Path) -> Path:
    hdr = read_header(ab)
    if hdr.encryption.lower() != "none":
        raise AbError("this backup is encrypted; it needs abe and the backup password")
    d = zlib.decompressobj() if hdr.compressed else None
    with ab.open("rb") as src, tar_out.open("wb") as out:
        src.seek(hdr.offset)
        while True:
            block = src.read(1 << 20)
            if not block:
                break
            out.write(d.decompress(block) if d else block)
        if d:
            out.write(d.flush())
    return tar_out


def extract_tar(tar_path: Path, dest: Path) -> int:
    """Extract with the 'data' filter, which refuses absolute paths, links out of the tree and device files."""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except tarfile.ReadError:
            # adb backups often end without the two zero blocks tar expects; members read so far are kept.
            pass
        count = sum(1 for p in dest.rglob("*") if p.is_file())
    return count


def make_test_ab(tar_bytes: bytes, path: Path) -> Path:
    """Build an unencrypted, compressed .ab around a tar. Used by tests."""
    path.write_bytes(MAGIC + b"\n5\n1\nnone\n" + zlib.compress(tar_bytes))
    return path
