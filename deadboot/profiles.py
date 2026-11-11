"""ROM profiles: one TOML file per ROM in data/roms (shipped) or the user's roms folder.

A profile carries what the package type alone cannot: where to get the files,
how releases are verified, relock and root rules, a minimum fastboot version,
and steps the project's guide asks for.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import hostplatform, packages

VERIFY_METHODS = {"sha256", "ssh-keygen", "gpg", "minisign", "none"}
KNOWN_KEYS = {
    "id", "name", "description", "package_types", "install_guide_url", "downloads_url", "devices_url",
    "xda_url", "verify", "signature", "relock_supported", "root_supported", "min_fastboot", "pixel_only",
    "checklist", "pre_steps", "post_steps", "kind",
}
SIG_KEYS = {"namespace", "identity", "key_url", "key_file_name", "signature_suffix", "expect"}
STEP_KEYS = {"text", "action", "to"}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    id: str
    name: str
    description: str = ""
    package_types: list[str] = field(default_factory=list)
    install_guide_url: str = ""
    downloads_url: str = ""
    devices_url: str = ""
    xda_url: str = ""
    verify: str = "sha256"
    signature: dict = field(default_factory=dict)
    relock_supported: bool = False
    root_supported: bool = True
    min_fastboot: str = ""
    pixel_only: bool = False
    kind: str = "rom"                 # "stock" or "rom"
    checklist: list[str] = field(default_factory=list)
    pre_steps: list[dict] = field(default_factory=list)
    post_steps: list[dict] = field(default_factory=list)
    source: Path | None = None

    def url(self, which: str, codename: str) -> str:
        raw = getattr(self, f"{which}_url", "")
        return raw.replace("<codename>", codename or "") if raw else ""


def validate(raw: dict, source: str) -> Profile:
    unknown = set(raw) - KNOWN_KEYS
    if unknown:
        raise ProfileError(f"{source}: unknown key(s) {', '.join(sorted(unknown))}")
    for req in ("id", "name"):
        if not isinstance(raw.get(req), str) or not raw[req]:
            raise ProfileError(f"{source}: '{req}' is required")
    verify = raw.get("verify", "sha256")
    if verify not in VERIFY_METHODS:
        raise ProfileError(f"{source}: verify must be one of {', '.join(sorted(VERIFY_METHODS))}")
    types = raw.get("package_types", [])
    valid_types = set(packages.TYPE_LABELS) - {packages.UNKNOWN}
    bad = [t for t in types if t not in valid_types]
    if bad:
        raise ProfileError(f"{source}: unknown package type(s) {', '.join(bad)}")
    sig = raw.get("signature", {})
    if not isinstance(sig, dict) or set(sig) - SIG_KEYS:
        raise ProfileError(f"{source}: [signature] has unknown keys {', '.join(sorted(set(sig) - SIG_KEYS))}")
    if verify == "ssh-keygen" and not (sig.get("namespace") and sig.get("identity")):
        raise ProfileError(f"{source}: ssh-keygen verification needs signature.namespace and signature.identity")
    for key in ("pre_steps", "post_steps"):
        for i, step in enumerate(raw.get(key, [])):
            if not isinstance(step, dict) or "text" not in step or set(step) - STEP_KEYS:
                raise ProfileError(f"{source}: {key}[{i + 1}] needs 'text' and only text/action/to")
    mf = raw.get("min_fastboot", "")
    if mf and not all(p.isdigit() for p in mf.split(".")):
        raise ProfileError(f"{source}: min_fastboot must look like 35.0.1")
    kind = raw.get("kind", "rom")
    if kind not in ("rom", "stock"):
        raise ProfileError(f"{source}: kind must be 'rom' or 'stock'")
    return Profile(
        id=raw["id"], name=raw["name"], description=raw.get("description", ""), package_types=types,
        install_guide_url=raw.get("install_guide_url", ""), downloads_url=raw.get("downloads_url", ""),
        devices_url=raw.get("devices_url", ""), xda_url=raw.get("xda_url", ""), verify=verify, signature=sig,
        relock_supported=bool(raw.get("relock_supported", False)),
        root_supported=bool(raw.get("root_supported", True)), min_fastboot=mf,
        pixel_only=bool(raw.get("pixel_only", False)), kind=kind, checklist=list(raw.get("checklist", [])),
        pre_steps=list(raw.get("pre_steps", [])), post_steps=list(raw.get("post_steps", [])),
    )


def load_file(path: Path) -> Profile:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path.name}: {exc}") from exc
    prof = validate(raw, path.name)
    prof.source = path
    return prof


def load_all(extra_dirs: list[Path] | None = None) -> tuple[dict[str, Profile], list[str]]:
    """Shipped profiles, then the user's own (which can override a shipped id). Returns (profiles, errors)."""
    dirs = [hostplatform.data_resource("roms"), hostplatform.user_roms_dir()] + (extra_dirs or [])
    out: dict[str, Profile] = {}
    errors: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            try:
                prof = load_file(f)
            except (ProfileError, OSError) as exc:
                errors.append(str(exc))
                continue
            out[prof.id] = prof
    if "generic" not in out:
        out["generic"] = Profile(id="generic", name="Generic", verify="sha256")
    return out, errors


def max_min_fastboot(profiles: dict[str, Profile]) -> str:
    best: tuple[int, ...] = ()
    best_s = ""
    for p in profiles.values():
        if p.min_fastboot:
            v = tuple(int(x) for x in p.min_fastboot.split("."))
            if v > best:
                best, best_s = v, p.min_fastboot
    return best_s


def relock_safe(profile: Profile | None, package_kind: str, verified: bool) -> bool:
    """Whether a finished install may count toward allowing relock.

    The profile must support relocking, the package type must be one the profile installs
    (so picking the wrong profile in the dropdown can't unlock relock), and when the profile
    has a verification method, the package must have passed it.
    """
    if profile is None or not profile.relock_supported:
        return False
    if package_kind not in profile.package_types:
        return False
    if profile.verify != "none" and not verified:
        return False
    return True
