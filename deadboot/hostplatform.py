"""The only module that knows whether we run on Linux or Windows.

Everything else asks this module for paths, download names, script names,
USB guidance and filename rules instead of checking the OS itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def resource_dir() -> Path:
    """Where the shipped data/ folder lives (repo checkout, AppImage or PyInstaller bundle)."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent


def data_resource(*parts: str) -> Path:
    return resource_dir().joinpath("data", *parts)


def config_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "deADBoot"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "deadboot"


def data_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "deADBoot"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "deadboot"


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return config_dir() / "settings.toml"


def platform_tools_dir() -> Path:
    return data_dir() / "platform-tools"


def scrcpy_dir() -> Path:
    return data_dir() / "scrcpy"


def recipes_dir() -> Path:
    return data_dir() / "recipes"


def logs_dir() -> Path:
    return data_dir() / "logs"


def cache_dir() -> Path:
    return data_dir() / "cache"


def keys_dir() -> Path:
    return data_dir() / "keys"


def device_dir(serial: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in serial)
    return data_dir() / "devices" / safe


def user_roms_dir() -> Path:
    return data_dir() / "roms"


def exe(name: str) -> str:
    return name + ".exe" if IS_WINDOWS else name


def platform_tools_url() -> str:
    """Google's always-latest platform-tools zip for this OS."""
    osname = "windows" if IS_WINDOWS else "linux"
    return f"https://dl.google.com/android/repository/platform-tools-latest-{osname}.zip"


def scrcpy_asset_pattern() -> str | None:
    """Regex for the scrcpy release asset to fetch, or None where we rely on the distro."""
    if IS_WINDOWS:
        return r"^scrcpy-win64-.*\.zip$"
    return None


def flash_all_script_name() -> str:
    return "flash-all.bat" if IS_WINDOWS else "flash-all.sh"


def script_command(script: Path) -> list[str]:
    """How to run a vendor flash script without relying on its file mode."""
    if IS_WINDOWS:
        return ["cmd", "/c", str(script)]
    return ["bash", str(script)]


def path_sep() -> str:
    return ";" if IS_WINDOWS else ":"


def usb_setup_kind() -> str:
    return "drivers" if IS_WINDOWS else "udev"


def rename_pulled_files() -> bool:
    """Android filenames are only rewritten on Windows."""
    return IS_WINDOWS
