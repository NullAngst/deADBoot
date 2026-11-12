"""Debloat without root: disable or remove-for-user, always reversible, with a do-not-touch list."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import hostplatform

VENDOR_PREFIXES = {
    "All": (),
    "Carrier": ("com.verizon", "com.vzw", "com.att", "com.tmobile", "com.sprint", "com.mobitv", "com.dti.",
                "com.locationlabs", "com.motricity", "com.telecom", "com.vodafone", "com.orange", "com.cricket",
                "com.metropcs", "com.uscc", "us.com.dt.iq", "com.aura.oobe"),
    "Samsung": ("com.samsung", "com.sec.", "com.osp.", "com.knox", "com.sem."),
    "Google": ("com.google.", "com.android.chrome", "com.android.vending"),
    "Facebook": ("com.facebook.",),
    "Microsoft": ("com.microsoft.", "com.skype", "com.linkedin"),
    "Xiaomi": ("com.miui", "com.xiaomi", "com.mi."),
    "Motorola": ("com.motorola",),
    "OnePlus / Oppo": ("com.oneplus", "com.oplus", "com.coloros", "com.heytap"),
    "Huawei": ("com.huawei",),
    "LG": ("com.lge", "com.lg."),
    "Amazon": ("com.amazon",),
    "Netflix / Spotify": ("com.netflix", "com.spotify"),
}


def load_protected(extra: Path | None = None) -> set[str]:
    files = [hostplatform.data_resource("debloat-protected.txt")]
    if extra:
        files.append(extra)
    user = hostplatform.data_dir() / "debloat-protected.txt"
    files.append(user)
    out: set[str] = set()
    for f in files:
        if f.is_file():
            out |= read_list(f)
    return out


@dataclass
class CommonBloat:
    """The common-bloat list: exact package names and vendor-family prefixes, each with a group name."""

    exact: dict[str, str] = field(default_factory=dict)
    prefixes: list[tuple[str, str]] = field(default_factory=list)

    def group(self, package: str) -> str:
        """The group a package belongs to, or '' when it is not on the list."""
        if package in self.exact:
            return self.exact[package]
        for prefix, group in self.prefixes:
            if package.startswith(prefix):
                return group
        return ""

    def __len__(self) -> int:
        return len(self.exact) + len(self.prefixes)


def parse_common(text: str, into: CommonBloat | None = None) -> CommonBloat:
    out = into if into is not None else CommonBloat()
    group = "common bloat"
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            group = line.lstrip("#").strip() or "common bloat"
            continue
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith(".*"):
            out.prefixes.append((line[:-1], group))      # keep the trailing dot: com.facebook.
        else:
            out.exact[line] = group
    return out


def load_common() -> CommonBloat:
    """The shipped common-bloat list plus the user's own additions."""
    out = CommonBloat()
    for f in (hostplatform.data_resource("debloat-common.txt"), hostplatform.data_dir() / "debloat-common.txt"):
        if f.is_file():
            parse_common(f.read_text(encoding="utf-8", errors="replace"), out)
    return out


def read_list(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def read_list_ordered(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line not in out:
            out.append(line)
    return out


def write_list(path: Path, packages: list[str], header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if header:
        lines += [f"# {h}" for h in header.splitlines()]
    lines += sorted(set(packages))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def component_package(component: str) -> str:
    """'com.foo/.Main' or 'com.foo/com.foo.Main' -> 'com.foo'."""
    return component.strip().split("/", 1)[0] if component else ""


def matches_vendor(package: str, vendor: str) -> bool:
    prefixes = VENDOR_PREFIXES.get(vendor, ())
    return not prefixes or package.startswith(prefixes)


def disable_cmd(pkg: str) -> list[str]:
    return ["shell", "pm", "disable-user", "--user", "0", pkg]


def remove_cmd(pkg: str) -> list[str]:
    return ["shell", "pm", "uninstall", "-k", "--user", "0", pkg]


def restore_cmd(pkg: str, mode: str) -> list[str]:
    if mode == "disable":
        return ["shell", "pm", "enable", "--user", "0", pkg]
    return ["shell", "cmd", "package", "install-existing", pkg]
