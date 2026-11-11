"""USB access guidance: udev on Linux, drivers on Windows.

deADBoot never asks for root or admin and never writes outside your home
directory. It detects what is missing and shows you the commands to run.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import hostplatform

RULES_LOCATIONS = (
    "/etc/udev/rules.d/51-android.rules",
    "/lib/udev/rules.d/51-android.rules",
    "/usr/lib/udev/rules.d/51-android.rules",
    "/usr/lib/udev/rules.d/70-android-tools.rules",
    "/lib/udev/rules.d/70-android-tools-adb.rules",
    "/usr/lib/udev/rules.d/70-android-tools-adb.rules",
)

# Package manager -> (package providing Android udev rules, install command)
RULES_PACKAGES = {
    "apt": ("android-sdk-platform-tools-common", "sudo apt install android-sdk-platform-tools-common"),
    "dnf": ("android-tools", "sudo dnf install android-tools"),
    "zypper": ("android-udev-rules", "sudo zypper install android-udev-rules"),
    "pacman": ("android-udev", "sudo pacman -S android-udev"),
    "emerge": ("dev-util/android-udev-rules", "sudo emerge --ask dev-util/android-udev-rules"),
    "xbps-install": ("android-udev-rules", "sudo xbps-install android-udev-rules"),
    "apk": ("", ""),
}

# USB vendor IDs for adb, fastboot and Samsung download mode. Vendor-level rules cover every mode.
VENDORS = {
    "18d1": "Google", "04e8": "Samsung", "22b8": "Motorola", "2a70": "OnePlus", "2717": "Xiaomi",
    "0bb4": "HTC", "12d1": "Huawei", "1004": "LG", "0fce": "Sony", "2ae5": "Fairphone", "05c6": "Qualcomm",
    "0e8d": "MediaTek", "1949": "Amazon", "0b05": "Asus", "17ef": "Lenovo", "19d2": "ZTE", "1bbb": "Alcatel",
    "2a45": "Meizu", "0502": "Acer", "413c": "Dell", "2b4c": "Realme", "22d9": "OPPO", "2d95": "vivo",
    "29a9": "Nothing", "2970": "Wileyfox", "1ebf": "Coolpad", "2916": "Yota", "0489": "Foxconn",
    "1d4d": "Pegatron", "2080": "Barnes & Noble", "091e": "Garmin-Asus", "0482": "Kyocera",
    "10a9": "Pantech", "1f53": "Teleepoch", "2340": "Teleepoch", "2257": "Unihertz",
}

WINDOWS_DRIVER_PAGES = {
    "google": "https://developer.android.com/studio/run/win-usb",
    "samsung": "https://developer.samsung.com/android-usb-driver",
    "motorola": "https://en-us.support.motorola.com/app/usb-drivers",
    "oneplus": "https://www.oneplus.com/support/softwareupgrade",
    "sony": "https://developer.sony.com/open-source/aosp-on-xperia-open-devices/downloads/drivers",
    "xiaomi": "https://www.mi.com/global/service/support/",
    "_oem": "https://developer.android.com/studio/run/oem-usb",
}
ZADIG_URL = "https://zadig.akeo.ie/"


def detect_package_manager() -> str | None:
    for pm in ("apt", "dnf", "zypper", "pacman", "emerge", "xbps-install", "apk"):
        if shutil.which(pm):
            return pm
    return None


def installed_rules_file() -> str | None:
    for loc in RULES_LOCATIONS:
        if os.path.isfile(loc):
            return loc
    return None


def rules_group(rules_text: str | None, pm: str | None) -> str:
    if rules_text:
        for group in ("adbusers", "plugdev"):
            if f'GROUP="{group}"' in rules_text or f"GROUP=\"{group}\"" in rules_text:
                return group
    return "adbusers" if pm == "pacman" else "plugdev"


def user_groups() -> set[str]:
    try:
        import grp
        return {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (ImportError, KeyError, OSError):
        return set()


def group_exists(name: str) -> bool:
    try:
        import grp
        grp.getgrnam(name)
        return True
    except (ImportError, KeyError):
        return False


def rules_file_content(group: str) -> str:
    lines = ["# Android devices: adb, fastboot and Samsung download mode, written from deADBoot's guide"]
    for vid, name in sorted(VENDORS.items(), key=lambda kv: kv[1].lower()):
        lines.append(f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid}", MODE="0660", GROUP="{group}", '
                     f'TAG+="uaccess"  # {name}')
    return "\n".join(lines) + "\n"


@dataclass
class UdevAdvice:
    rules_found: str | None
    package_manager: str | None
    group: str
    in_group: bool
    commands: str


def udev_advice() -> UdevAdvice:
    pm = detect_package_manager()
    found = installed_rules_file()
    text = None
    if found:
        try:
            text = Path(found).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
    group = rules_group(text, pm)
    in_group = group in user_groups()
    blocks: list[str] = []
    step = 1
    pkg_cmd = RULES_PACKAGES.get(pm or "", ("", ""))[1]
    if not found:
        if pkg_cmd:
            blocks.append(f"# {step}. Install your distro's Android udev rules package ({pm} detected)\n{pkg_cmd}")
            step += 1
        blocks.append(
            f"# {step}. Or, with no package, write the rules file yourself. Read it first, then run:\n"
            "sudo tee /etc/udev/rules.d/51-android.rules >/dev/null <<'EOF'\n"
            + rules_file_content(group) + "EOF")
        step += 1
        blocks.append(f"# {step}. Reload udev\nsudo udevadm control --reload-rules\nsudo udevadm trigger")
        step += 1
    if not in_group:
        create = "" if group_exists(group) else f"sudo groupadd {group}\n"
        blocks.append(f"# {step}. Add yourself to the {group} group, then log out and back in for it to apply\n"
                      f'{create}sudo usermod -aG {group} "$USER"')
        step += 1
    blocks.append(f"# {step}. Unplug the device and plug it back in")
    return UdevAdvice(found, pm, group, in_group, "\n\n".join(blocks))


def windows_driver_page(manufacturer: str | None) -> str:
    key = (manufacturer or "").strip().lower()
    for name, url in WINDOWS_DRIVER_PAGES.items():
        if name != "_oem" and name in key:
            return url
    return WINDOWS_DRIVER_PAGES["_oem"]


def windows_driver_steps(manufacturer: str | None, in_bootloader: bool) -> str:
    page = windows_driver_page(manufacturer)
    who = manufacturer or "your phone's maker"
    lines = [
        "Windows can see the phone but adb or fastboot cannot. That is a driver problem.",
        "",
        f"1. Download the USB driver for {who}: {page}",
        "2. Unzip it somewhere you'll keep it.",
        "3. Open Device Manager (Win+X, Device Manager).",
        "4. Find the phone. It is usually under 'Other devices' with a yellow mark, or under 'Portable Devices'.",
        "5. Right-click it, Update driver, Browse my computer, then pick the folder from step 2.",
        "6. Unplug and replug, then press Check again.",
    ]
    if in_bootloader:
        lines.insert(2, "This often shows up only in the bootloader: normal adb works through Windows' generic "
                        "driver, fastboot needs the vendor one. Do the steps below with the phone in fastboot mode.")
    return "\n".join(lines)


def zadig_steps() -> str:
    return "\n".join([
        "Samsung download mode on Windows needs the WinUSB driver bound to the device for heimdall.",
        "",
        f"1. Download Zadig: {ZADIG_URL}",
        "2. Put the phone in download mode and plug it in.",
        "3. In Zadig: Options, List All Devices, then pick the Samsung device (often 'Gadget Serial' or 'MSM8960').",
        "4. Pick WinUSB as the target driver and press Replace Driver.",
        "5. This replaces Samsung's own driver for that mode. Odin will stop seeing the phone in download mode "
        "until you reinstall Samsung's driver.",
    ])


def usb_kind() -> str:
    return hostplatform.usb_setup_kind()
