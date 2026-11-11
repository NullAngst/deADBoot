"""Magisk patched-boot-image flow: pick the partition, find the exact stock image, check the patch.

Magisk does the patching on the phone. deADBoot gets the right stock image to
it, pulls the result back, sanity-checks it and keeps the stock copy as the
way back.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import hostplatform

BUILD_PROPS = ("ro.build.fingerprint", "ro.build.id", "ro.build.version.incremental", "ro.boot.slot_suffix",
               "ro.product.device")
BOOT_MAGIC = b"ANDROID!"
PATCHED_RE = re.compile(r"^magisk_patched-[\w.\-]+\.img$")


@dataclass
class BuildInfo:
    fingerprint: str
    build_id: str
    incremental: str
    slot_suffix: str
    codename: str

    @classmethod
    def from_props(cls, props: dict[str, str]) -> "BuildInfo":
        return cls(props.get("ro.build.fingerprint", ""), props.get("ro.build.id", ""),
                   props.get("ro.build.version.incremental", ""), props.get("ro.boot.slot_suffix", ""),
                   props.get("ro.product.device", ""))

    @property
    def slot(self) -> str:
        return self.slot_suffix.lstrip("_")


def choose_partition(has_init_boot_var: str | None, package_has_init_boot: bool | None) -> tuple[str | None, str]:
    """Both checks must agree. has_init_boot_var is fastboot getvar has-slot:init_boot ('yes'/'no'/None).

    The partition getvar returns 'yes' for A/B devices that have init_boot. A device without the partition
    answers 'no' or an error. package_has_init_boot is None when no package is in play (root read off device).
    """
    var = (has_init_boot_var or "").strip().lower()
    if var not in ("yes", "no"):
        return None, ("Could not read has-slot:init_boot from the bootloader. Reboot to the bootloader and "
                      "read the device state first.")
    device_has = var == "yes"
    if package_has_init_boot is not None and package_has_init_boot != device_has:
        return None, ("The bootloader and the firmware package disagree about init_boot "
                      f"(bootloader says {'yes' if device_has else 'no'}, package "
                      f"{'has' if package_has_init_boot else 'has no'} init_boot.img). Stopping; this usually "
                      "means the package is for a different device or Android version.")
    part = "init_boot" if device_has else "boot"
    return part, f"Target partition: {part}."


@dataclass
class PatchCheck:
    ok: bool
    message: str


def check_patched(patched: Path, stock: Path) -> PatchCheck:
    if not patched.is_file() or patched.stat().st_size == 0:
        return PatchCheck(False, "The patched image is empty or missing.")
    with patched.open("rb") as fh:
        head = fh.read(8)
    if head != BOOT_MAGIC:
        return PatchCheck(False, "The patched file does not start with the Android boot image header (ANDROID!).")
    if stock.is_file() and patched.stat().st_size != stock.stat().st_size:
        return PatchCheck(False, f"Size mismatch: patched {patched.stat().st_size} bytes, stock "
                                 f"{stock.stat().st_size} bytes. Stopping.")
    return PatchCheck(True, "Patched image looks sane: header present, same size as the stock image.")


def newest_patched(ls_names: list[str]) -> str | None:
    cands = sorted(n for n in ls_names if PATCHED_RE.match(n))
    return cands[-1] if cands else None


def build_dir(serial: str, build_id: str) -> Path:
    safe = re.sub(r"[^\w.\-]", "_", build_id or "unknown")
    return hostplatform.device_dir(serial) / safe


def stock_path(serial: str, build_id: str, partition: str) -> Path:
    return build_dir(serial, build_id) / f"stock-{partition}.img"


def patched_path(serial: str, build_id: str, partition: str) -> Path:
    return build_dir(serial, build_id) / f"magisk-{partition}.img"


def saved_builds(serial: str) -> list[Path]:
    base = hostplatform.device_dir(serial)
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and any(d.glob("stock-*.img"))]
    return sorted(dirs, key=lambda d: d.stat().st_mtime)


def cleanup_old_builds(serial: str, keep: int = 2) -> list[Path]:
    dirs = saved_builds(serial)
    removed = dirs[:-keep] if len(dirs) > keep else []
    for d in removed:
        shutil.rmtree(d, ignore_errors=True)
    return removed


def latest_saved_stock(serial: str) -> tuple[Path, str] | None:
    """Most recent saved stock image and its partition, for Restore stock."""
    for d in reversed(saved_builds(serial)):
        for part in ("init_boot", "boot"):
            p = d / f"stock-{part}.img"
            if p.is_file():
                return p, part
    return None
