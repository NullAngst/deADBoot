"""Polls adb and fastboot, keeps per-device details, and signals changes.

Refreshes on a timer and on USB hotplug (Linux: a watch on /dev/bus/usb).
Polls never overlap: a new poll starts only after the last one finished.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from .. import hostplatform, parsers
from ..runner import Command, remote_quote

STATE_LABELS = {
    "device": "ready", "unauthorized": "unauthorized", "offline": "offline", "recovery": "recovery",
    "sideload": "sideload", "bootloader": "bootloader", "fastbootd": "fastbootd", "download": "download mode",
    "no-permissions": "no permissions", "rescue": "rescue", "authorizing": "authorizing",
}


@dataclass
class DeviceInfo:
    serial: str
    state: str
    source: str = "adb"
    model: str = ""
    codename: str = ""
    manufacturer: str = ""
    android: str = ""
    sdk: int = 0
    battery: int | None = None
    storage_free_kb: int | None = None
    lock: str = "unknown"
    slot: str = ""
    root: bool = False
    props: dict = field(default_factory=dict)
    fastboot_vars: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        name = self.model.replace("_", " ") or self.codename or self.serial
        return f"{name} ({STATE_LABELS.get(self.state, self.state)})"

    @property
    def is_tcp(self) -> bool:
        return ":" in self.serial

    @property
    def adb_ready(self) -> bool:
        return self.state == "device"

    @property
    def in_fastboot(self) -> bool:
        return self.state in ("bootloader", "fastbootd")

    @property
    def confirm_name(self) -> str:
        """What the person types to confirm a destructive action."""
        return (self.model or self.codename or self.serial).replace("_", " ")


class DeviceModel(QObject):
    changed = pyqtSignal(list)            # list[DeviceInfo]
    device_updated = pyqtSignal(object)   # DeviceInfo
    windows_driver_hint = pyqtSignal(str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.devices: dict[str, DeviceInfo] = {}
        self._polling = False
        self._details_pending: set[str] = set()
        self._win_ticks = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.watcher = QFileSystemWatcher(self)
        if hostplatform.IS_LINUX and os.path.isdir("/dev/bus/usb"):
            paths = ["/dev/bus/usb"] + [os.path.join("/dev/bus/usb", d) for d in os.listdir("/dev/bus/usb")]
            self.watcher.addPaths([p for p in paths if os.path.isdir(p)])
            self.watcher.directoryChanged.connect(lambda _p: QTimer.singleShot(800, self.poll))

    def start(self) -> None:
        self.timer.start(max(1, self.app.settings.poll_seconds) * 1000)
        self.poll()

    def stop(self) -> None:
        self.timer.stop()

    # ------------------------------------------------------------ polling

    def poll(self) -> None:
        if self._polling or not self.app.tools.available("adb"):
            return
        self._polling = True

        def work(_w):
            runner = self.app.runner
            found: list[parsers.DeviceEntry] = []
            r = runner.run_blocking(Command("adb", ["devices", "-l"]), quiet=True)
            found += parsers.parse_adb_devices(r.out)
            if self.app.tools.available("fastboot"):
                r = runner.run_blocking(Command("fastboot", ["devices"]), quiet=True)
                for d in parsers.parse_fastboot_devices(r.text):
                    if d.state == "bootloader":
                        v = runner.run_blocking(Command("fastboot", ["getvar", "is-userspace"], d.serial,
                                                        timeout_s=5), quiet=True)
                        if parsers.parse_getvar(v.text).get("is-userspace", "").lower() == "yes":
                            d.state = "fastbootd"
                    found.append(d)
            if self.app.tools.available("heimdall"):
                r = runner.run_blocking(Command("heimdall", ["detect"], timeout_s=8), quiet=True)
                if r.ok and "Device detected" in r.text:
                    found.append(parsers.DeviceEntry("samsung-download", "download", source="heimdall"))
            return found

        self.app.jobs.run(work, self._poll_done, lambda e: self._poll_failed(e))

    def _poll_failed(self, _err: str) -> None:
        self._polling = False

    def _poll_done(self, found: list) -> None:
        self._polling = False
        seen = set()
        changed = False
        for entry in found:
            seen.add(entry.serial)
            cur = self.devices.get(entry.serial)
            if cur is None:
                cur = DeviceInfo(entry.serial, entry.state, entry.source,
                                 model=entry.props.get("model", ""), codename=entry.props.get("device", ""))
                self.devices[entry.serial] = cur
                changed = True
                self._details(cur)
            elif cur.state != entry.state:
                cur.state = entry.state
                cur.source = entry.source
                changed = True
                self._details(cur)
        for serial in list(self.devices):
            if serial not in seen:
                del self.devices[serial]
                changed = True
        if changed:
            self.changed.emit(list(self.devices.values()))
        if hostplatform.IS_WINDOWS and not self.devices:
            self._win_ticks += 1
            if self._win_ticks % 8 == 1:
                self._windows_check()

    def refresh(self, serial: str) -> None:
        d = self.devices.get(serial)
        if d:
            self._details(d, force=True)

    def _details(self, dev: DeviceInfo, force: bool = False) -> None:
        if dev.serial in self._details_pending and not force:
            return
        if dev.state not in ("device", "recovery", "bootloader", "fastbootd"):
            return
        self._details_pending.add(dev.serial)
        serial, state = dev.serial, dev.state

        def work(_w):
            runner = self.app.runner
            info: dict = {}
            if state in ("device", "recovery"):
                r = runner.run_blocking(Command("adb", ["shell", "getprop"], serial, timeout_s=20), quiet=True)
                info["props"] = parsers.parse_getprop(r.out)
                if state == "device":
                    r = runner.run_blocking(Command("adb", ["shell", "dumpsys battery"], serial, timeout_s=15),
                                            quiet=True)
                    info["battery"] = parsers.battery_level(r.out)
                    r = runner.run_blocking(Command("adb", ["shell", "df -k /data"], serial, timeout_s=15), quiet=True)
                    df = parsers.parse_df(r.out)
                    info["free"] = df["avail"] if df else None
                    r = runner.run_blocking(Command("adb", ["shell", remote_quote("sh", "-c", "command -v su")],
                                                    serial, timeout_s=10), quiet=True)
                    info["su"] = bool(r.out.strip())
            else:
                r = runner.run_blocking(Command("fastboot", ["getvar", "all"], serial, timeout_s=30), quiet=True)
                info["vars"] = parsers.parse_getvar(r.text)
            return info

        def done(info: dict) -> None:
            self._details_pending.discard(serial)
            d = self.devices.get(serial)
            if not d:
                return
            props = info.get("props")
            if props:
                d.props = props
                d.model = props.get("ro.product.model", d.model)
                d.codename = props.get("ro.product.device", d.codename)
                d.manufacturer = props.get("ro.product.manufacturer", d.manufacturer)
                d.android = props.get("ro.build.version.release", "")
                sdk = props.get("ro.build.version.sdk", "0")
                d.sdk = int(sdk) if sdk.isdigit() else 0
                d.slot = props.get("ro.boot.slot_suffix", "").lstrip("_")
                locked = props.get("ro.boot.flash.locked")
                vb = props.get("ro.boot.vbmeta.device_state", "")
                d.lock = "locked" if locked == "1" or vb == "locked" else "unlocked" if locked == "0" or \
                    vb == "unlocked" else "unknown"
            if "battery" in info:
                d.battery = info["battery"]
            if "free" in info:
                d.storage_free_kb = info["free"]
            if "su" in info:
                d.root = info["su"]
            v = info.get("vars")
            if v:
                d.fastboot_vars = v
                d.codename = v.get("product", d.codename)
                d.slot = v.get("current-slot", d.slot)
                u = v.get("unlocked", "").lower()
                d.lock = "unlocked" if u == "yes" else "locked" if u == "no" else d.lock
                if not d.model:
                    d.model = v.get("product", "")
            self.device_updated.emit(d)

        self.app.jobs.run(work, done, lambda _e: self._details_pending.discard(serial))

    # ------------------------------------------------------------ windows drivers

    def _windows_check(self) -> None:
        """A phone Windows sees but adb and fastboot don't: point at the driver fix."""
        from ..usbhelp import VENDORS

        def work(_w):
            r = self.app.runner.run_blocking(Command("pnputil", ["/enum-devices", "/connected"], timeout_s=20),
                                             quiet=True)
            text = r.out.upper()
            for vid, name in VENDORS.items():
                if f"VID_{vid.upper()}" in text:
                    return name
            return ""

        self.app.jobs.run(work, lambda name: name and self.windows_driver_hint.emit(name))
