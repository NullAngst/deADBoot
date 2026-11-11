"""Device operations shared by the tabs and by recipes.

Every operation runs through the runner. With planning=True nothing executes:
each command is recorded instead, which is how a recipe's plan and the
"show me the commands" view are produced from the same code that does the work.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import debloat as debloat_mod
from . import hostplatform, parsers
from .runner import Command, Result, remote_quote

ADB_MODES = {"device", "recovery", "sideload", "rescue"}
FASTBOOT_MODES = {"bootloader", "fastbootd"}
REBOOT_TARGETS = ("system", "bootloader", "fastbootd", "recovery", "sideload")
SHIZUKU_PKG = "moe.shizuku.privileged.api"
PLAY_PKG = "com.android.vending"
BOOTLOADER_ONLY = {"bootloader", "radio", "abl", "xbl", "tz"}


class OpError(RuntimeError):
    pass


@dataclass
class PlanItem:
    text: str
    kind: str = "cmd"         # cmd, note, wait
    writes: bool = False
    wipes: bool = False
    step: int = -1


@dataclass
class DebloatSummary:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class DeviceOps:
    def __init__(self, runner, serial: str, planning: bool = False, cancel: threading.Event | None = None,
                 mode_hint: str = "device"):
        self.runner = runner
        self.serial = serial
        self.planning = planning
        self.cancel = cancel or threading.Event()
        self.plan: list[PlanItem] = []
        self.step_no = -1
        self.mode_hint = mode_hint     # planning: the mode we assume the device is in
        self.on_status: Callable[[str], None] | None = None

    # ------------------------------------------------------------ plumbing

    def status(self, text: str) -> None:
        if self.planning:
            return
        if self.on_status:
            self.on_status(text)
        self.runner.line.emit(f"# {text}")

    def note(self, text: str, kind: str = "note") -> None:
        if self.planning:
            self.plan.append(PlanItem(text, kind=kind, step=self.step_no))
        else:
            self.runner.line.emit(f"# {text}")

    def _exec(self, cmd: Command, wipes: bool = False, on_line=None) -> Result:
        if self.planning:
            self.plan.append(PlanItem(self.runner.display(cmd), writes=cmd.writes, wipes=wipes, step=self.step_no))
            return Result(0, "", "", dry=True)
        if self.cancel.is_set():
            return Result(-15, "", "cancelled")
        return self.runner.run_blocking(cmd, on_line=on_line, cancel=self.cancel)

    def _query(self, cmd: Command) -> Result | None:
        """Read-only lookups. In planning they are skipped (they answer nothing useful about the future)."""
        if self.planning:
            return None
        return self.runner.run_blocking(cmd, quiet=True, cancel=self.cancel)

    def adb(self, args: list[str], writes: bool = False, wipes: bool = False, timeout: float | None = None,
            on_line=None) -> Result:
        return self._exec(Command("adb", args, self.serial, writes=writes, timeout_s=timeout), wipes, on_line)

    def fastboot(self, args: list[str], writes: bool = False, wipes: bool = False, on_line=None) -> Result:
        return self._exec(Command("fastboot", args, self.serial, writes=writes), wipes, on_line)

    def shell(self, remote: str, writes: bool = False, timeout: float | None = None) -> Result:
        return self.adb(["shell", remote], writes=writes, timeout=timeout)

    def qshell(self, remote: str, timeout: float | None = 30) -> str:
        r = self._query(Command("adb", ["shell", remote], self.serial, timeout_s=timeout))
        return r.out if r else ""

    # ------------------------------------------------------------ state

    def getprop(self) -> dict[str, str]:
        r = self._query(Command("adb", ["shell", "getprop"], self.serial, timeout_s=30))
        return parsers.parse_getprop(r.out) if r else {}

    def prop(self, name: str) -> str:
        return self.qshell(remote_quote("getprop", name)).strip()

    def getvar(self, name: str) -> str | None:
        r = self._query(Command("fastboot", ["getvar", name], self.serial, timeout_s=20))
        if r is None:
            return None
        vals = parsers.parse_getvar(r.text)
        if name in vals:
            return vals[name]
        for k, v in vals.items():
            if k.lower() == name.lower():
                return v
        return None

    def getvar_all(self) -> dict[str, str]:
        r = self._query(Command("fastboot", ["getvar", "all"], self.serial, timeout_s=60))
        return parsers.parse_getvar(r.text) if r else {}

    def state(self) -> str | None:
        """Current mode of this serial, from adb devices and fastboot devices."""
        if self.planning:
            return self.mode_hint
        r = self.runner.run_blocking(Command("adb", ["devices", "-l"]), quiet=True)
        for d in parsers.parse_adb_devices(r.out):
            if d.serial == self.serial:
                return d.state
        if self.runner.tools.available("fastboot"):
            r = self.runner.run_blocking(Command("fastboot", ["devices"]), quiet=True)
            for d in parsers.parse_fastboot_devices(r.text):
                if d.serial == self.serial:
                    if d.state == "bootloader":
                        uv = self.getvar("is-userspace")
                        return "fastbootd" if (uv or "").lower() == "yes" else "bootloader"
                    return d.state
        return None

    def wait_for(self, want: str, timeout: float = 300, poll: float = 1.5) -> bool:
        """Wait until the device reaches a mode. 'fastboot' accepts bootloader or fastbootd."""
        if self.planning:
            self.mode_hint = "device" if want == "system" else want
            return True
        accept = {want}
        if want == "fastboot":
            accept = FASTBOOT_MODES
        if want == "system":
            accept = {"device"}
        self.status(f"Waiting for the device to reach {want} mode")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                return False
            if self.state() in accept:
                return True
            self.cancel.wait(poll)
        return False

    def battery(self) -> int | None:
        if self.planning:
            return None
        return parsers.battery_level(self.qshell("dumpsys battery"))

    def lock_state(self) -> str:
        """'locked', 'unlocked' or 'unknown', from adb props or fastboot getvar."""
        st = self.state()
        if st in FASTBOOT_MODES:
            v = (self.getvar("unlocked") or "").lower()
            return "unlocked" if v == "yes" else "locked" if v == "no" else "unknown"
        if st in ADB_MODES:
            p = self.getprop()
            if p.get("ro.boot.flash.locked") in ("0", "1"):
                return "locked" if p["ro.boot.flash.locked"] == "1" else "unlocked"
            vb = p.get("ro.boot.vbmeta.device_state", "")
            if vb in ("locked", "unlocked"):
                return vb
        return "unknown"

    # ------------------------------------------------------------ reboot

    def reboot(self, to: str, wait: bool = True) -> Result:
        if to not in REBOOT_TARGETS:
            raise OpError(f"unknown reboot target {to}")
        mode = self.state() or "device"
        if mode in FASTBOOT_MODES:
            args = {"system": ["reboot"], "bootloader": ["reboot", "bootloader"],
                    "fastbootd": ["reboot", "fastboot"], "recovery": ["reboot", "recovery"]}.get(to)
            if args is None:
                raise OpError("From fastboot, go to recovery first, then choose Apply from ADB for sideload.")
            res = self.fastboot(args, writes=True)
        else:
            args = {"system": ["reboot"], "bootloader": ["reboot", "bootloader"],
                    "fastbootd": ["reboot", "fastboot"], "recovery": ["reboot", "recovery"],
                    "sideload": ["reboot", "sideload"]}[to]
            res = self.adb(args, writes=True)
        if res.ok and wait:
            target = {"system": "device"}.get(to, to)
            if not self.wait_for(target, timeout=240):
                return Result(1, res.out, f"device did not reach {to} mode in time")
        return res

    # ------------------------------------------------------------ fastboot actions

    def _slot_suffix(self) -> str:
        cur = (self.getvar("current-slot") or "").strip().lstrip("_")
        return f"_{cur}" if cur in ("a", "b") else ""

    def flash(self, partition: str, image: str, slots: str = "active", disable_verity: bool = False) -> Result:
        """Flash one partition, rebooting between bootloader and fastbootd as the partition needs."""
        extra = ["--disable-verity", "--disable-verification"] if disable_verity else []
        if self.planning:
            target = partition if slots != "active" else f"{partition}_<active slot>"
            args = extra + ["flash"] + (["--slot=all"] if slots == "all" else []) + [target, image]
            self.note(f"(reboots to fastbootd first if {partition} is a logical partition)")
            return self.fastboot(args, writes=True)
        mode = self.state()
        logical = (self.getvar(f"is-logical:{partition}") or "").lower() == "yes"
        if logical and mode == "bootloader":
            self.status(f"{partition} is a logical partition; it is flashed from fastbootd")
            r = self.reboot("fastbootd")
            if not r.ok:
                return r
        elif partition in BOOTLOADER_ONLY and mode == "fastbootd":
            r = self.reboot("bootloader")
            if not r.ok:
                return r
        has_slot = (self.getvar(f"has-slot:{partition}") or "").lower() == "yes"
        if has_slot and slots == "all":
            args = extra + ["flash", "--slot=all", partition, image]
        elif has_slot and slots == "active":
            suffix = self._slot_suffix()
            self.status(f"Writing {partition}{suffix} (active slot)")
            args = extra + ["flash", f"{partition}{suffix}", image]
        else:
            args = extra + ["flash", partition, image]
        return self.fastboot(args, writes=True)

    def boot_image(self, image: str) -> Result:
        return self.fastboot(["boot", image], writes=True)

    def set_active_slot(self, slot: str) -> Result:
        if slot not in ("a", "b"):
            raise OpError("slot must be a or b")
        return self.fastboot([f"--set-active={slot}"], writes=True)

    def unlock(self, code: str = "") -> Result:
        if code:
            return self.fastboot(["oem", "unlock", code], writes=True, wipes=True)
        r = self.fastboot(["flashing", "unlock"], writes=True, wipes=True)
        if not self.planning and not r.ok and re.search(r"unknown|not (supported|recognized)|invalid", r.text, re.I):
            self.status("flashing unlock is not supported here; trying the older oem unlock")
            r = self.fastboot(["oem", "unlock"], writes=True, wipes=True)
        return r

    def lock(self) -> Result:
        return self.fastboot(["flashing", "lock"], writes=True, wipes=True)

    def fastboot_update(self, zip_path: str, wipe: bool) -> Result:
        return self.fastboot((["-w"] if wipe else []) + ["update", zip_path], writes=True, wipes=wipe)

    def flash_all(self, script_dir: str, keep_data: bool = False) -> Result:
        """Run the package's own flash-all script against this serial."""
        name = hostplatform.flash_all_script_name()
        script = Path(script_dir) / name
        if not self.planning and not script.is_file():
            return Result(1, "", f"{name} not found in {script_dir}")
        run = script
        if keep_data:
            run = script.with_name(script.stem + ".deadboot-keepdata" + script.suffix)
            if not self.planning:
                text = script.read_text(encoding="utf-8", errors="replace")
                run.write_text(strip_wipe(text), encoding="utf-8")
            self.note("Using a copy of the script with the -w wipe flag removed. Keeping data across a "
                      "downgrade or a ROM switch usually fails.")
        cmd = Command("script", [str(run)], self.serial, writes=True, cwd=str(script.parent))
        return self._exec(cmd, wipes=not keep_data)

    # ------------------------------------------------------------ recovery

    def sideload(self, zip_path: str, accept_unsigned: bool = False) -> Result:
        if accept_unsigned:
            self.note("Recovery may warn the package is not signed with its key. That is expected for "
                      "Google apps packages and Magisk: choose Yes on the phone.")
        r = self.adb(["sideload", zip_path], writes=True, timeout=None)
        if not self.planning and not r.ok and "Total xfer" in r.text:
            # Some recoveries close the connection after a successful transfer; adb then exits non-zero.
            return Result(0, r.out, r.err)
        return r

    # ------------------------------------------------------------ apps

    def install(self, apks: list[str], reinstall: bool = False, downgrade: bool = False,
                grant: bool = False, bypass_low_sdk: bool = False) -> Result:
        flags = []
        if reinstall:
            flags.append("-r")
        if downgrade:
            flags.append("-d")
        if grant:
            flags.append("-g")
        if bypass_low_sdk:
            flags.append("--bypass-low-target-sdk-block")
        verb = "install" if len(apks) == 1 else "install-multiple"
        return self.adb([verb] + flags + list(apks), writes=True)

    def uninstall(self, package: str, keep_data: bool) -> Result:
        if keep_data:
            return self.adb(["shell", "pm", "uninstall", "-k", package], writes=True)
        return self.adb(["uninstall", package], writes=True, wipes=True)

    def package_installed(self, package: str) -> bool:
        if self.planning:
            return False
        return bool(parsers.parse_pm_path(self.qshell(remote_quote("pm", "path", package))))

    def apk_paths(self, package: str) -> list[str]:
        return parsers.parse_pm_path(self.qshell(remote_quote("pm", "path", package)))

    def abi(self) -> str:
        return self.prop("ro.product.cpu.abi") if not self.planning else ""

    def has_play(self) -> bool:
        return self.package_installed(PLAY_PKG)

    def root_available(self) -> bool:
        if self.planning:
            return False
        out = self.qshell("su -c id", timeout=25)
        return "uid=0" in out

    def push(self, local: str, remote: str) -> Result:
        return self.adb(["push", local, remote], writes=True)

    def pull(self, remote: str, local: str, preserve: bool = False) -> Result:
        return self.adb(["pull"] + (["-a"] if preserve else []) + [remote, local])

    def open_play_listing(self, package: str) -> Result:
        return self.shell(remote_quote("am", "start", "-a", "android.intent.action.VIEW", "-d",
                                       f"market://details?id={package}"), writes=True)

    # ------------------------------------------------------------ debloat

    def dynamic_protected(self) -> set[str]:
        if self.planning:
            return set()
        out = set()
        home = self.qshell("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                           "-c android.intent.category.HOME")
        lines = [ln.strip() for ln in home.splitlines() if "/" in ln]
        if lines:
            out.add(debloat_mod.component_package(lines[-1]))
        ime = self.qshell("settings get secure default_input_method").strip()
        if ime and ime != "null":
            out.add(debloat_mod.component_package(ime))
        return out

    def installed_package_names(self, include_disabled: bool = True) -> set[str]:
        if self.planning:
            return set()
        names = parsers.parse_package_names(self.qshell("pm list packages -u" if include_disabled
                                                        else "pm list packages"))
        return names

    def debloat(self, packages: list[str], mode: str, protected: set[str], record=None,
                model: str = "") -> DebloatSummary:
        if mode not in ("remove", "disable"):
            raise OpError("debloat mode must be remove or disable")
        summary = DebloatSummary()
        guard = set(protected) | self.dynamic_protected()
        installed = self.installed_package_names()
        if self.planning:
            self.note("Packages on the list that are not installed are skipped and listed in the summary.")
        for pkg in packages:
            if pkg in guard:
                summary.refused.append(pkg)
                if self.planning:
                    self.note(f"refused (do-not-touch list): {pkg}")
                continue
            if not self.planning and pkg not in installed:
                summary.skipped.append(pkg)
                continue
            args = debloat_mod.remove_cmd(pkg) if mode == "remove" else debloat_mod.disable_cmd(pkg)
            r = self.adb(args, writes=True)
            ok = r.ok and ("Failure" not in r.text) and ("Error" not in r.text)
            if ok:
                summary.done.append(pkg)
                if record is not None and not self.planning:
                    record.record_debloat(pkg, mode, model)
            else:
                summary.failed.append(pkg)
        return summary

    def restore_package(self, pkg: str, mode: str) -> Result:
        return self.adb(debloat_mod.restore_cmd(pkg, mode), writes=True)

    # ------------------------------------------------------------ shizuku

    def shizuku_starter(self) -> tuple[str | None, str]:
        """Find Shizuku's own start method from the installed package. Returns (remote command, how)."""
        paths = self.apk_paths(SHIZUKU_PKG)
        if paths:
            base_dir = paths[0].rsplit("/", 1)[0]
            found = self.qshell(f"ls {base_dir}/lib/*/libshizuku.so 2>/dev/null").split()
            libs = [f for f in found if f.endswith("libshizuku.so")]
            if libs:
                return libs[0], "Shizuku's bundled starter (libshizuku.so) from the installed package"
        scripts = ("/storage/emulated/0/Android/data/moe.shizuku.privileged.api/start.sh",
                   "/sdcard/Android/data/moe.shizuku.privileged.api/start.sh")
        for attempt in range(2):
            for script in scripts:
                if self.qshell(f"[ -f {script} ] && echo yes").strip() == "yes":
                    return f"sh {script}", "the start.sh script Shizuku writes when its app is opened"
            if attempt == 0 and paths:
                # Shizuku writes start.sh the first time its app opens. Open it once and look again.
                self.status("Opening the Shizuku app once so it writes its start script")
                self.shell(remote_quote("monkey", "-p", SHIZUKU_PKG, "-c", "android.intent.category.LAUNCHER", "1"),
                           writes=True, timeout=30)
                self.cancel.wait(4)
        return None, ("Could not find Shizuku's start method. Open the Shizuku app on the phone once, "
                      "then try again.")

    def shizuku_running(self) -> bool:
        if self.planning:
            return False
        return bool(self.qshell("pidof shizuku_server").strip())

    def shizuku_start(self, root: bool = False) -> Result:
        if self.planning:
            how = "su -c <Shizuku starter>" if root else "<Shizuku starter found from the installed package>"
            return self.shell(how, writes=True)
        starter, how = self.shizuku_starter()
        if not starter:
            return Result(1, "", how)
        self.status(f"Starting Shizuku with {how}" + (" in root mode" if root else ""))
        remote = f"su -c {remote_quote(starter)}" if root else starter
        r = self.shell(remote, writes=True, timeout=60)
        if r.ok or "shizuku_starter exit with 0" in r.text or "info: shizuku_starter" in r.text:
            time.sleep(1.5)
            if self.shizuku_running():
                return Result(0, r.out, r.err)
        return Result(r.code or 1, r.out, r.err or "Shizuku did not start; see the log.")

    # ------------------------------------------------------------ magisk helpers

    def read_partition_as_root(self, partition: str, slot_suffix: str, local: str) -> Result:
        remote_tmp = f"/data/local/tmp/deadboot-{partition}.img"
        src = f"/dev/block/by-name/{partition}{slot_suffix}"
        r = self.shell(f"su -c {remote_quote(f'dd if={src} of={remote_tmp} && chmod 644 {remote_tmp}')}",
                       writes=False, timeout=120)
        if not r.ok:
            return r
        p = self.pull(remote_tmp, local)
        self.shell(f"su -c {remote_quote(f'rm -f {remote_tmp}')}")
        return p

    def list_download_dir(self) -> list[str]:
        return [ln.strip() for ln in self.qshell("ls -1 /sdcard/Download/").splitlines() if ln.strip()]


def strip_wipe(script_text: str) -> str:
    """Remove fastboot's -w (wipe) flag from a flash-all script's update call."""
    out = []
    for line in script_text.splitlines(keepends=True):
        if "fastboot" in line and "update" in line:
            line = re.sub(r"(\s)-w(\s)", r"\1\2", line)
        out.append(line)
    return "".join(out)


def extract_zip_to(zip_path: Path, dest: Path) -> Path:
    """Extract a factory image's outer zip (the folder holding flash-all) with Python's zipfile."""
    import zipfile
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        root = dest.resolve()
        for info in zf.infolist():
            if not str((dest / info.filename).resolve()).startswith(str(root)):
                raise OpError(f"unsafe path in zip: {info.filename}")
        zf.extractall(dest)
    return dest


def find_script_dir(root: Path) -> Path | None:
    name = hostplatform.flash_all_script_name()
    for p in root.rglob(name):
        return p.parent
    return None
