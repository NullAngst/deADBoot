"""ROM package detection, device matching and file verification.

Packages are identified by what is inside them, never by filename. Zips are
opened read-only. Signature checks shell out to ssh-keygen, gpg or minisign
through the runner; nothing here implements cryptography itself.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import parsers

FACTORY = "factory_image"
FASTBOOT_UPDATE = "fastboot_update"
RECOVERY_ZIP = "recovery_zip"
OTA_PAYLOAD = "ota_payload"
LOOSE_IMAGES = "loose_images"
UNKNOWN = "unknown"

TYPE_LABELS = {
    FACTORY: "Factory image (runs the package's own flash-all script)",
    FASTBOOT_UPDATE: "Fastboot update (fastboot update, or per-partition flashes)",
    RECOVERY_ZIP: "Recovery zip (boot the ROM's recovery, then adb sideload)",
    OTA_PAYLOAD: "OTA payload zip (adb sideload from a recovery that accepts it)",
    LOOSE_IMAGES: "Loose images (you map each image to a partition)",
    UNKNOWN: "Unrecognized. Only the loose-image path is offered.",
}


@dataclass
class PackageInfo:
    path: Path
    kind: str = UNKNOWN
    android_info: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    asserted_devices: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    inner_zip: str = ""            # factory image: image-<codename>-<build>.zip
    flash_script_dir: str = ""     # folder holding flash-all inside the outer zip
    build: str = ""                # build ID or version string from the package
    notes: list[str] = field(default_factory=list)

    @property
    def has_init_boot(self) -> bool:
        return any(Path(i).name == "init_boot.img" for i in self.images)

    @property
    def target_devices(self) -> list[str]:
        devs: list[str] = []
        for key in ("product", "board"):
            devs += self.android_info.get(key, [])
        for key in ("pre-device",):
            if self.metadata.get(key):
                devs += [d.strip() for d in self.metadata[key].split(",") if d.strip()]
        devs += self.asserted_devices
        seen, out = set(), []
        for d in devs:
            if d.lower() not in seen:
                seen.add(d.lower())
                out.append(d)
        return out


def parse_android_info(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^require\s+(?:partition-exists=\S+\s+)?([A-Za-z0-9_.\-]+)\s*=\s*(.+)$", line)
        if m:
            out.setdefault(m.group(1), []).extend(v.strip() for v in m.group(2).split("|") if v.strip())
    return out


def parse_metadata(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


_ASSERT_RE = re.compile(r'getprop\("ro\.(?:product\.|build\.product|product\.device)[a-z.]*"\)\s*==\s*"([^"]+)"')


def parse_updater_asserts(text: str) -> list[str]:
    return sorted(set(_ASSERT_RE.findall(text)))


def _read(zf: zipfile.ZipFile, name: str, limit: int = 1 << 20) -> str:
    with zf.open(name) as fh:
        return fh.read(limit).decode("utf-8", errors="replace")


def _build_from_inner(name: str) -> str:
    # image-caiman-ap4a.250105.002.zip / image-caiman-2025010100.zip
    m = re.match(r"^image-[^-]+-(.+)\.zip$", Path(name).name)
    return m.group(1) if m else ""


def _inspect_zip(path: Path, info: PackageInfo) -> None:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        base = {n: Path(n).name for n in names}
        scripts = [n for n in names if base[n] in ("flash-all.sh", "flash-all.bat")]
        inner = [n for n in names if re.match(r"^image-.+\.zip$", base[n])]
        if scripts and inner:
            info.kind = FACTORY
            info.inner_zip = inner[0]
            info.flash_script_dir = str(Path(scripts[0]).parent) if Path(scripts[0]).parent != Path(".") else ""
            info.build = _build_from_inner(inner[0])
            # Inner image zips are a few GB; read only what is needed.
            with zf.open(inner[0]) as fh:
                try:
                    with zipfile.ZipFile(fh) as innerzf:
                        info.images = [n for n in innerzf.namelist() if n.endswith(".img")]
                        if "android-info.txt" in innerzf.namelist():
                            info.android_info = parse_android_info(_read(innerzf, "android-info.txt"))
                except zipfile.BadZipFile:
                    info.notes.append("The inner image zip could not be read.")
            return
        if "payload.bin" in names and "META-INF/com/android/metadata" in names:
            info.kind = OTA_PAYLOAD
            info.metadata = parse_metadata(_read(zf, "META-INF/com/android/metadata"))
            if "META-INF/com/google/android/updater-script" in names:
                info.asserted_devices = parse_updater_asserts(_read(zf, "META-INF/com/google/android/updater-script"))
            info.build = info.metadata.get("post-build-incremental", "") or info.metadata.get("post-build", "")
            return
        if "META-INF/com/google/android/update-binary" in names:
            info.kind = RECOVERY_ZIP
            if "META-INF/com/android/metadata" in names:
                info.metadata = parse_metadata(_read(zf, "META-INF/com/android/metadata"))
            if "META-INF/com/google/android/updater-script" in names:
                info.asserted_devices = parse_updater_asserts(_read(zf, "META-INF/com/google/android/updater-script"))
            info.build = info.metadata.get("post-build", "")
            return
        imgs = [n for n in names if n.endswith(".img")]
        if "android-info.txt" in names and imgs:
            info.kind = FASTBOOT_UPDATE
            info.android_info = parse_android_info(_read(zf, "android-info.txt"))
            info.images = imgs
            return
        if imgs and all(n.endswith(".img") or n.endswith("/") for n in names):
            info.kind = LOOSE_IMAGES
            info.images = imgs
            return


def detect(path: Path) -> PackageInfo:
    path = Path(path)
    info = PackageInfo(path=path)
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file()]
        names = {p.name: p for p in files}
        inner = [p for p in files if re.match(r"^image-.+\.zip$", p.name)]
        if ("flash-all.sh" in names or "flash-all.bat" in names) and inner:
            info.kind = FACTORY
            info.inner_zip = str(inner[0])
            info.flash_script_dir = str((names.get("flash-all.sh") or names["flash-all.bat"]).parent)
            info.build = _build_from_inner(inner[0].name)
            try:
                _inspect_inner_file(info, inner[0])
            except zipfile.BadZipFile:
                info.notes.append("The inner image zip could not be read.")
            return info
        imgs = [str(p) for p in files if p.suffix == ".img"]
        if "android-info.txt" in names and imgs:
            info.kind = FASTBOOT_UPDATE
            info.android_info = parse_android_info(names["android-info.txt"].read_text(errors="replace"))
            info.images = imgs
            return info
        if imgs:
            info.kind = LOOSE_IMAGES
            info.images = imgs
        return info
    if path.suffix.lower() == ".img":
        info.kind = LOOSE_IMAGES
        info.images = [str(path)]
        return info
    if zipfile.is_zipfile(path):
        try:
            _inspect_zip(path, info)
        except (zipfile.BadZipFile, OSError) as exc:
            info.notes.append(f"Could not read the zip: {exc}")
    return info


def _inspect_inner_file(info: PackageInfo, inner: Path) -> None:
    with zipfile.ZipFile(inner) as zf:
        info.images = [n for n in zf.namelist() if n.endswith(".img")]
        if "android-info.txt" in zf.namelist():
            info.android_info = parse_android_info(_read(zf, "android-info.txt"))


# ---------------------------------------------------------------- device matching

@dataclass
class Match:
    ok: bool                # nothing contradicts the device
    blocked: bool           # a definite mismatch: no override
    checked_by: str         # "metadata", "filename", "nothing"
    message: str
    warnings: list[str] = field(default_factory=list)


def match_device(pkg: PackageInfo, codename: str, fastboot_product: str = "",
                 bootloader_version: str = "", baseband_version: str = "") -> Match:
    device_names = {n.lower() for n in (codename, fastboot_product) if n}
    targets = pkg.target_devices
    warnings: list[str] = []
    if targets:
        if not device_names:
            return Match(False, True, "metadata",
                         "The package names its target device but this device's codename is unknown. "
                         "Connect it over adb or in fastboot mode so it can be read.")
        if not device_names & {t.lower() for t in targets}:
            return Match(False, True, "metadata",
                         f"This package is built for {', '.join(targets)}. The connected device is "
                         f"{', '.join(sorted(device_names))}. Flashing is blocked.")
        want_bl = pkg.android_info.get("version-bootloader", [])
        if want_bl and bootloader_version and bootloader_version not in want_bl:
            warnings.append(f"The package expects bootloader {' or '.join(want_bl)}; the device has "
                            f"{bootloader_version}. Factory images flash their own bootloader; for other packages "
                            "an old bootloader under a new ROM is a common bootloop cause.")
        want_bb = pkg.android_info.get("version-baseband", [])
        if want_bb and baseband_version and baseband_version not in want_bb:
            warnings.append(f"The package expects radio {' or '.join(want_bb)}; the device has {baseband_version}.")
        return Match(True, False, "metadata",
                     f"The package's own metadata targets {', '.join(targets)}, which matches this device. "
                     "That proves it was built for this device, not that the build is good.", warnings)
    name = pkg.path.name.lower()
    for dn in device_names:
        if re.search(rf"(^|[^a-z0-9]){re.escape(dn)}([^a-z0-9]|$)", name):
            return Match(True, False, "filename",
                         f"The package has no device metadata. Its filename mentions {dn}. That filename check "
                         "is all deADBoot could verify.", warnings)
    return Match(True, False, "nothing",
                 "The package has no device metadata and its filename does not name this device. deADBoot "
                 "cannot check that it is for this device. Only continue if the ROM's own guide tells you to "
                 "flash this exact file.", warnings)


def rollback_warning(pkg: PackageInfo, device_build_id: str, device_build_utc: str = "") -> str:
    """Warn when the package looks older than what is on the device."""
    pkg_ts = pkg.metadata.get("post-timestamp", "")
    if pkg_ts.isdigit() and device_build_utc.isdigit():
        if int(pkg_ts) < int(device_build_utc):
            return ("This package's build is older than the one on the device. Devices with anti-rollback "
                    "protection (Pixel 6 and newer) can refuse to boot it.")
        return ""
    a = parsers.build_id_date(pkg.build.upper())
    b = parsers.build_id_date(device_build_id.upper())
    if a and b and a < b:
        return (f"This image's build date ({a}) is older than the device's ({b}). Devices with anti-rollback "
                "protection (Pixel 6 and newer) can refuse to boot an older image.")
    return ""


def build_matches(pkg_build: str, device_build_id: str, device_incremental: str = "") -> bool:
    pb = pkg_build.lower()
    return bool(pb) and pb in {device_build_id.lower(), device_incremental.lower()}


# ---------------------------------------------------------------- extraction for Magisk

def extract_from_factory(pkg: PackageInfo, partition: str, dest: Path) -> Path:
    """Pull only <partition>.img out of the factory zip's inner image zip."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    want = f"{partition}.img"
    if pkg.path.is_dir():
        with zipfile.ZipFile(pkg.inner_zip) as inner:
            with inner.open(want) as src, dest.open("wb") as out:
                _copy(src, out)
        return dest
    with zipfile.ZipFile(pkg.path) as outer, outer.open(pkg.inner_zip) as fh, zipfile.ZipFile(fh) as inner:
        with inner.open(want) as src, dest.open("wb") as out:
            _copy(src, out)
    return dest


def extract_member(zip_path: Path, member: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as src, dest.open("wb") as out:
        _copy(src, out)
    return dest


def _copy(src, out) -> None:
    while True:
        block = src.read(1 << 20)
        if not block:
            break
        out.write(block)
