"""Find, version-check and locate the external tools deADBoot drives.

Nothing is bundled. Tools come from PATH, a directory set in Settings, or the
platform-tools / scrcpy copy fetched into the data directory on request.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import hostplatform, parsers
from .settings import Settings

PLATFORM_TOOLS = ("adb", "fastboot")

# What to tell people to install when a tool is missing.
PACKAGE_HINTS = {
    "adb": "android-tools / android-sdk-platform-tools, or let deADBoot fetch Google's platform-tools",
    "fastboot": "android-tools / android-sdk-platform-tools, or let deADBoot fetch Google's platform-tools",
    "scrcpy": "scrcpy (your distro's package, or the release from github.com/Genymobile/scrcpy)",
    "heimdall": "heimdall-flash or heimdall (or the fork LineageOS links in its Samsung guides)",
    "payload-dumper-go": "payload-dumper-go (github.com/ssut/payload-dumper-go releases)",
    "ssh-keygen": "openssh-client (Windows: Settings, Optional features, OpenSSH Client)",
    "gpg": "gnupg",
    "minisign": "minisign",
    "abe": "Android Backup Extractor (abe), only needed for encrypted .ab backups",
}

PAYLOAD_DUMPERS = ("payload-dumper-go", "payload_dumper", "payload-dumper")


@dataclass
class ToolStatus:
    name: str
    path: str | None
    version: tuple[int, ...] | None = None
    source: str = ""              # path, settings, fetched

    @property
    def found(self) -> bool:
        return self.path is not None


class Tools:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: dict[str, ToolStatus] = {}

    def refresh(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------ resolution

    def _fetched_platform_tool(self, name: str) -> Path | None:
        p = hostplatform.platform_tools_dir() / hostplatform.exe(name)
        return p if p.is_file() else None

    def _resolve(self, name: str) -> ToolStatus:
        s = self.settings
        if name in PLATFORM_TOOLS:
            if s.platform_tools_dir:
                p = Path(s.platform_tools_dir) / hostplatform.exe(name)
                if p.is_file():
                    return ToolStatus(name, str(p), source="settings")
            fetched = self._fetched_platform_tool(name)
            if s.force_fetched_tools and fetched:
                return ToolStatus(name, str(fetched), source="fetched")
            found = shutil.which(name)
            if found:
                return ToolStatus(name, found, source="path")
            if fetched:
                return ToolStatus(name, str(fetched), source="fetched")
            return ToolStatus(name, None)
        if name == "scrcpy":
            if s.scrcpy_path and Path(s.scrcpy_path).is_file():
                return ToolStatus(name, s.scrcpy_path, source="settings")
            found = shutil.which("scrcpy")
            if found:
                return ToolStatus(name, found, source="path")
            for p in hostplatform.scrcpy_dir().rglob(hostplatform.exe("scrcpy")) if hostplatform.scrcpy_dir().is_dir() else []:
                return ToolStatus(name, str(p), source="fetched")
            return ToolStatus(name, None)
        if name == "heimdall":
            if s.heimdall_path and Path(s.heimdall_path).is_file():
                return ToolStatus(name, s.heimdall_path, source="settings")
            found = shutil.which("heimdall")
            return ToolStatus(name, found, source="path" if found else "")
        if name == "payload-dumper":
            if s.payload_dumper_path and Path(s.payload_dumper_path).is_file():
                return ToolStatus(name, s.payload_dumper_path, source="settings")
            for cand in PAYLOAD_DUMPERS:
                found = shutil.which(cand)
                if found:
                    return ToolStatus(name, found, source="path")
            return ToolStatus(name, None)
        if name == "abe":
            if s.abe_path and Path(s.abe_path).is_file():
                return ToolStatus(name, s.abe_path, source="settings")
            found = shutil.which("abe")
            return ToolStatus(name, found, source="path" if found else "")
        found = shutil.which(name)
        return ToolStatus(name, found, source="path" if found else "")

    def status(self, name: str) -> ToolStatus:
        if name not in self._cache:
            self._cache[name] = self._resolve(name)
        return self._cache[name]

    def path(self, name: str) -> str | None:
        return self.status(name).path

    def available(self, name: str) -> bool:
        return self.path(name) is not None

    def argv(self, name: str) -> list[str] | None:
        """Base argv for a tool. A .jar path (abe.jar) is run through java."""
        p = self.path(name)
        if p is None:
            return None
        if p.lower().endswith(".jar"):
            java = shutil.which("java")
            return [java, "-jar", p] if java else None
        return [p]

    def hint(self, name: str) -> str:
        return PACKAGE_HINTS.get(name, name)

    # ------------------------------------------------------------ versions

    def detect_versions(self, runner) -> dict[str, ToolStatus]:
        """Run each found tool's version command through the runner. Call from a worker thread."""
        from .runner import Command
        checks = {
            "adb": (["version"], parsers.parse_adb_version),
            "fastboot": (["--version"], parsers.parse_fastboot_version),
            "scrcpy": (["--version"], parsers.parse_version),
            "heimdall": (["version"], parsers.parse_version),
        }
        for name, (args, parse) in checks.items():
            st = self.status(name)
            if not st.found:
                continue
            res = runner.run_blocking(Command(name, args), quiet=True)
            st.version = parse(res.text)
        ssh = shutil.which("ssh")
        if self.available("ssh-keygen") and ssh:
            res = runner.run_blocking(Command("ssh", ["-V"]), quiet=True)
            self.status("ssh-keygen").version = parsers.parse_version(res.text.replace("p", "."))
        return dict(self._cache)

    def ssh_keygen_can_verify(self) -> bool:
        """ssh-keygen -Y verify needs OpenSSH 8.1 or newer. Unknown version counts as yes; the run says."""
        st = self.status("ssh-keygen")
        if not st.found:
            return False
        return st.version is None or parsers.version_at_least(st.version, "8.1")
