"""Per-device records: what deADBoot last flashed (for the relock rule) and debloat history."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from . import hostplatform


class DeviceState:
    def __init__(self, serial: str, base: Path | None = None):
        self.serial = serial
        self.dir = (base / serial) if base else hostplatform.device_dir(serial)
        self.file = self.dir / "state.json"
        self.data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.file)

    # ------------------------------------------------------------ relock rule

    def record_flash(self, profile_id: str, kind: str, relock_supported: bool, what: str) -> None:
        self.data["last_flash"] = {
            "profile": profile_id, "kind": kind, "relock_supported": relock_supported, "what": what,
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        self.data["magisk_since_last_flash"] = False
        self.save()

    def record_magisk_flash(self) -> None:
        self.data["magisk_since_last_flash"] = True
        self.data["rooted_by_deadboot"] = True
        self.save()

    def record_partial_flash(self, what: str) -> None:
        """A single-partition or loose-image flash: deADBoot no longer knows the device is relockable."""
        self.data["last_flash"] = {"profile": "", "kind": "partial", "relock_supported": False, "what": what,
                                   "time": _dt.datetime.now().isoformat(timespec="seconds")}
        self.save()

    def relock_allowed(self) -> tuple[bool, str]:
        last = self.data.get("last_flash")
        if not last:
            return False, ("deADBoot did not do the last flash on this device, so it cannot know what is on it. "
                           "Relocking is refused.")
        if self.data.get("magisk_since_last_flash"):
            return False, "A Magisk-patched image was flashed since the last ROM install. Relocking would brick."
        if not last.get("relock_supported"):
            return False, (f"The last flash ({last.get('what') or last.get('profile')}) is not a stock image or a "
                           "ROM whose profile supports relocking.")
        return True, f"The last flash was {last.get('what') or last.get('profile')}, which supports relocking."

    @property
    def installed_profile(self) -> str:
        return (self.data.get("last_flash") or {}).get("profile", "")

    # ------------------------------------------------------------ debloat record

    def debloat_record(self) -> dict[str, dict]:
        return dict(self.data.get("debloat", {}))

    def record_debloat(self, package: str, mode: str, model: str = "") -> None:
        self.data.setdefault("debloat", {})[package] = {
            "mode": mode, "model": model, "time": _dt.datetime.now().isoformat(timespec="seconds")}
        self.save()

    def forget_debloat(self, package: str) -> None:
        self.data.get("debloat", {}).pop(package, None)
        self.save()

    # ------------------------------------------------------------ allowed_signers tracking

    def note(self, key: str, value) -> None:
        self.data[key] = value
        self.save()
