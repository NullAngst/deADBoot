"""Parsers for adb, fastboot and device shell output. Pure functions, no Qt, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- versions


def parse_version(text: str) -> tuple[int, ...] | None:
    """First dotted version number in text, as a tuple. '35.0.2-12147458' -> (35, 0, 2)."""
    m = re.search(r"(\d+(?:\.\d+)+)", text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def version_at_least(found: tuple[int, ...] | None, wanted: str) -> bool:
    if found is None:
        return False
    want = tuple(int(p) for p in wanted.split("."))
    length = max(len(found), len(want))
    return found + (0,) * (length - len(found)) >= want + (0,) * (length - len(want))


def parse_adb_version(text: str) -> tuple[int, ...] | None:
    """adb prints 'Android Debug Bridge version 1.0.41' then 'Version 35.0.2-...'."""
    for line in (text or "").splitlines():
        if line.strip().lower().startswith("version "):
            return parse_version(line)
    return parse_version(text)


def parse_fastboot_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"fastboot version\s+(\S+)", text or "")
    return parse_version(m.group(1)) if m else parse_version(text)


def version_str(v: tuple[int, ...] | None) -> str:
    return ".".join(str(p) for p in v) if v else "unknown"


# ---------------------------------------------------------------- device lists

ADB_STATES = {
    "device", "unauthorized", "offline", "recovery", "sideload", "bootloader",
    "host", "rescue", "authorizing", "connecting",
}


@dataclass
class DeviceEntry:
    serial: str
    state: str
    props: dict = field(default_factory=dict)
    source: str = "adb"


def parse_adb_devices(text: str) -> list[DeviceEntry]:
    out: list[DeviceEntry] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        serial, rest = parts[0], parts[1].strip()
        if rest.startswith("no permissions"):
            state = "no-permissions"
            props_part = ""
            m = re.search(r"\]\s*(.*)$", rest)
            if m:
                props_part = m.group(1)
        else:
            tokens = rest.split()
            state = tokens[0]
            props_part = " ".join(tokens[1:])
        props = {}
        for tok in props_part.split():
            if ":" in tok:
                k, v = tok.split(":", 1)
                props[k] = v
        out.append(DeviceEntry(serial=serial, state=state, props=props, source="adb"))
    return out


def parse_fastboot_devices(text: str) -> list[DeviceEntry]:
    out: list[DeviceEntry] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("no permissions"):
            out.append(DeviceEntry(serial="(no permissions)", state="no-permissions", source="fastboot"))
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, mode = parts[0], parts[1]
        if mode == "no":
            state = "no-permissions"
        elif mode == "fastbootd":
            state = "fastbootd"
        else:
            state = "bootloader"
        out.append(DeviceEntry(serial=serial, state=state, source="fastboot"))
    return out


# ---------------------------------------------------------------- getprop / getvar

_GETPROP_RE = re.compile(r"^\[(?P<k>[^\]]+)\]: \[(?P<v>.*)\]$")


def parse_getprop(text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    pending_key = None
    pending_val: list[str] = []
    for line in (text or "").splitlines():
        if pending_key is not None:
            if line.endswith("]"):
                pending_val.append(line[:-1])
                props[pending_key] = "\n".join(pending_val)
                pending_key = None
            else:
                pending_val.append(line)
            continue
        m = _GETPROP_RE.match(line)
        if m:
            props[m.group("k")] = m.group("v")
            continue
        m2 = re.match(r"^\[(?P<k>[^\]]+)\]: \[(?P<v>.*)$", line)
        if m2:
            pending_key = m2.group("k")
            pending_val = [m2.group("v")]
    return props


_GETVAR_SUBKEYS = ("partition-size", "partition-type", "has-slot", "is-logical", "slot-successful",
                   "slot-unbootable", "slot-retry-count")


def parse_getvar(text: str) -> dict[str, str]:
    """Parse `fastboot getvar all` or a single `fastboot getvar x` (fastboot prints to stderr)."""
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("Finished.") or line.startswith("all:"):
            continue
        if line.startswith("(bootloader)"):
            line = line[len("(bootloader)"):].strip()
        if line.lower().startswith("getvar:"):
            continue
        key = value = None
        for sub in _GETVAR_SUBKEYS:
            if line.startswith(sub + ":"):
                rest = line[len(sub) + 1:]
                if ":" in rest:
                    part, value = rest.rsplit(":", 1)
                    key = f"{sub}:{part.strip()}"
                break
        if key is None:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


# ---------------------------------------------------------------- install failures

INSTALL_FAILURES: dict[str, tuple[str, str | None]] = {
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": (
        "The installed copy is signed with a different key. Updating would need uninstalling it first, "
        "and its data goes with it.", "uninstall_reinstall"),
    "INSTALL_FAILED_VERSION_DOWNGRADE": (
        "The APK is an older version than the one installed. Tick 'Allow downgrade' to install it anyway.",
        "downgrade"),
    "INSTALL_FAILED_DEPRECATED_SDK_VERSION": (
        "This Android version blocks apps that target a very old SDK. Retry with the low-target-SDK bypass.",
        "bypass_low_sdk"),
    "INSTALL_FAILED_ALREADY_EXISTS": (
        "The app is already installed. Tick 'Reinstall keeping data'.", "reinstall"),
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": ("Not enough free storage on the device.", None),
    "INSTALL_FAILED_OLDER_SDK": ("The app needs a newer Android version than this device runs.", None),
    "INSTALL_FAILED_NO_MATCHING_ABIS": (
        "The APK has no native code for this device's CPU. Get the build for this ABI, or a universal APK.",
        None),
    "INSTALL_FAILED_INVALID_APK": ("The APK is damaged or not a valid package.", None),
    "INSTALL_PARSE_FAILED_NO_CERTIFICATES": ("The APK is not signed. Android refuses unsigned APKs.", None),
    "INSTALL_PARSE_FAILED_NOT_APK": ("The file is not an APK.", None),
    "INSTALL_PARSE_FAILED_MANIFEST_MALFORMED": ("The APK's manifest is broken.", None),
    "INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES": (
        "The split APKs are signed with different keys. They have to come from the same build.", None),
    "INSTALL_FAILED_MISSING_SPLIT": (
        "This app is split into several APKs and some are missing. Install all of them together.", None),
    "INSTALL_FAILED_DUPLICATE_PERMISSION": (
        "Another installed app already defines a permission this app wants. Usually a different build of the "
        "same app.", None),
    "INSTALL_FAILED_CONFLICTING_PROVIDER": (
        "Another installed app already uses a content provider name this app needs.", None),
    "INSTALL_FAILED_TEST_ONLY": ("This is a test-only APK. It needs the -t flag to install.", None),
    "INSTALL_FAILED_USER_RESTRICTED": (
        "The device blocked the install. Check the phone for a prompt, or 'Install via USB' in developer "
        "options (some vendors require it).", None),
    "INSTALL_FAILED_VERIFICATION_FAILURE": (
        "Package verification rejected the app. Check the phone for a Play Protect prompt.", None),
    "INSTALL_FAILED_ABORTED": ("The install was cancelled, usually from a prompt on the phone.", None),
    "INSTALL_FAILED_SESSION_INVALID": ("The install session broke partway. Retry.", None),
    "INSTALL_FAILED_INTERNAL_ERROR": ("The package manager hit an internal error. Reboot the phone and retry.",
                                      None),
    "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE": (
        "The app shares a user ID with an installed app signed differently.", None),
    "INSTALL_FAILED_UID_CHANGED": (
        "Leftover data from an old install conflicts. Uninstall the app fully, then install.", None),
    "INSTALL_FAILED_MEDIA_UNAVAILABLE": ("The storage the app should go to is not available.", None),
    "INSTALL_FAILED_DEXOPT": ("Android failed to optimize the app's code. Usually a broken APK.", None),
    "INSTALL_FAILED_CPU_ABI_INCOMPATIBLE": ("The APK's native code does not match this CPU.", None),
    "INSTALL_FAILED_MULTIPACKAGE_INCONSISTENCY": ("The split APKs do not belong to the same app version.", None),
}


@dataclass
class InstallFailure:
    code: str
    sentence: str
    suggestion: str | None
    raw: str


def decode_install_failure(text: str) -> InstallFailure | None:
    m = re.search(r"\b(INSTALL_(?:FAILED|PARSE_FAILED)_[A-Z_]+)", text or "")
    if not m:
        if "Failure" in (text or "") or "failed to install" in (text or "").lower():
            return InstallFailure("UNKNOWN", "The install failed. The full output is in the log.", None, text)
        return None
    code = m.group(1)
    sentence, suggestion = INSTALL_FAILURES.get(
        code, ("The install failed with " + code + ". The full output is in the log.", None))
    return InstallFailure(code, sentence, suggestion, text)


# ---------------------------------------------------------------- remote listing

@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: int
    mtime: str
    perms: str
    link_target: str = ""


_LS_RE = re.compile(
    r"^(?P<perms>[\-dlcbsp][rwxsStT\-]{9}[+.@]?)\s+\d+\s+\S+\s+\S+\s+"
    r"(?P<size>\d+|\d+,\s*\d+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})\s+(?P<name>.+)$"
)


def parse_ls_la(text: str) -> list[RemoteEntry]:
    out: list[RemoteEntry] = []
    for line in (text or "").splitlines():
        m = _LS_RE.match(line.rstrip())
        if not m:
            continue
        name = m.group("name")
        target = ""
        perms = m.group("perms")
        if perms.startswith("l") and " -> " in name:
            name, target = name.split(" -> ", 1)
        if name in (".", ".."):
            continue
        size_txt = m.group("size")
        size = int(size_txt) if size_txt.isdigit() else 0
        out.append(RemoteEntry(
            name=name, is_dir=perms.startswith("d") or (perms.startswith("l") and target.endswith("/")),
            size=size, mtime=f"{m.group('date')} {m.group('time')}", perms=perms, link_target=target))
    return out


def parse_find_stat(text: str) -> list[tuple[int, int, str]]:
    """Lines of 'size mtime path' from `find DIR -type f -exec stat -c '%s %Y %n' {} +`."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        out.append((int(parts[0]), int(parts[1]), parts[2]))
    return out


# ---------------------------------------------------------------- packages

@dataclass
class PackageEntry:
    name: str
    path: str = ""
    version_code: str = ""
    installer: str = ""
    system: bool = False
    enabled: bool = True


def parse_pm_list(text: str, system: bool = False) -> list[PackageEntry]:
    """`pm list packages [-f] [-i] [--show-versioncode]`."""
    out: list[PackageEntry] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        body = line[len("package:"):]
        installer = ""
        version = ""
        m = re.search(r"\s+installer=(\S+)", body)
        if m:
            installer = "" if m.group(1) == "null" else m.group(1)
            body = body[:m.start()] + body[m.end():]
        m = re.search(r"\s+versionCode:(\S+)", body)
        if m:
            version = m.group(1)
            body = body[:m.start()] + body[m.end():]
        m = re.search(r"\s+uid:\S+", body)
        if m:
            body = body[:m.start()] + body[m.end():]
        body = body.strip()
        path = ""
        idx = body.rfind(".apk=")
        if idx != -1:
            path, name = body[:idx + 4], body[idx + 5:]
        else:
            name = body
        out.append(PackageEntry(name=name, path=path, version_code=version, installer=installer, system=system))
    return out


def parse_pm_path(text: str) -> list[str]:
    return [line.strip()[len("package:"):] for line in (text or "").splitlines()
            if line.strip().startswith("package:")]


def parse_package_names(text: str) -> set[str]:
    return {line.strip()[len("package:"):].split()[0] for line in (text or "").splitlines()
            if line.strip().startswith("package:")}


# ---------------------------------------------------------------- battery, storage, mdns

def parse_dumpsys_battery(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if ":" in line and line.startswith("  "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def battery_level(text: str) -> int | None:
    val = parse_dumpsys_battery(text).get("level")
    return int(val) if val and val.isdigit() else None


def parse_df(text: str) -> dict[str, int] | None:
    """`df -k <path>`: returns size/used/avail in KiB for the last data row."""
    rows = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    for row in reversed(rows):
        nums = [p for p in row if p.isdigit()]
        if len(nums) >= 3:
            size, used, avail = (int(n) for n in nums[:3])
            return {"size": size, "used": used, "avail": avail}
    return None


@dataclass
class MdnsService:
    name: str
    kind: str
    address: str


def parse_mdns_services(text: str) -> list[MdnsService]:
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].startswith("_adb"):
            out.append(MdnsService(parts[0], parts[1], parts[2]))
    return out


# ---------------------------------------------------------------- build ids

def build_id_date(build_id: str) -> str | None:
    """Pixel build IDs carry a yymmdd date: 'AP4A.250105.002' -> '250105'."""
    m = re.search(r"\.(\d{6})\.", build_id or "")
    return m.group(1) if m else None
