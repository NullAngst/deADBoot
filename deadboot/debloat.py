"""Debloat without root: disable or remove-for-user, always reversible, with a do-not-touch list."""

from __future__ import annotations

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
