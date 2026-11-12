"""Recipes: saved step lists run in order against one device.

Every step maps to an action deADBoot already has (ops.DeviceOps). The same
step code runs twice: once with planning=True to show the plan, then for real.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from . import debloat as debloat_mod
from . import downloads, hostplatform, packages, parsers, profiles, tomlw
from .ops import REBOOT_TARGETS, DeviceOps, OpError, extract_zip_to, find_script_dir
from .runner import Command

# action -> (required fields, optional fields, description)
ACTIONS: dict[str, tuple[set[str], set[str], str]] = {
    "reboot": ({"to"}, set(), "Reboot to system, bootloader, fastbootd, recovery or sideload"),
    "flash": ({"partition", "image"}, {"slots", "disable_verity"}, "Flash one partition"),
    "boot_image": ({"image"}, set(), "Boot an image once without flashing it"),
    "sideload": ({"file"}, {"accept_unsigned"}, "adb sideload a zip from recovery"),
    "flash_all": ({"package"}, {"keep_data"}, "Run a factory image's own flash-all script"),
    "fastboot_update": ({"package"}, {"wipe"}, "fastboot update with an image zip"),
    "install_apk": (set(), {"file", "source", "reinstall", "downgrade", "grant", "bypass_low_sdk"},
                    "Install an APK file or a named source (droidify, obtainium, magisk, ...)"),
    "push": ({"local", "remote"}, set(), "Push a file to the device"),
    "pull": ({"remote", "local"}, {"preserve"}, "Pull a file or folder from the device"),
    "debloat": ({"list"}, {"mode"}, "Remove or disable every package on a list"),
    "shizuku_setup": (set(), {"client", "install_with_options", "root"}, "Install and start Shizuku and a client"),
    "magisk_root": (set(), {"package"}, "Patch the matching boot image with Magisk and flash it"),
    "set_active_slot": ({"slot"}, set(), "Switch the active A/B slot"),
    "unlock": (set(), {"code"}, "Unlock the bootloader (wipes the device)"),
    "lock": (set(), set(), "Relock the bootloader (only when the relock rule allows it)"),
    "wait": ({"message"}, {"until"}, "Pause for something only a person can do on the phone"),
    "note": ({"message"}, set(), "Show text and continue"),
}
WAIT_STATES = {"sideload", "recovery", "bootloader", "fastbootd", "fastboot", "device"}
INPUT_KINDS = {"file", "package", "apk", "signature", "allowed_signers", "folder"}
TOP_KEYS = {"name", "description", "rom_profile", "devices", "inputs", "steps"}
INPUT_KEYS = {"id", "label", "kind", "hash", "optional"}
FLASHING = {"flash", "boot_image", "sideload", "flash_all", "fastboot_update", "magisk_root", "set_active_slot",
            "unlock", "lock"}
NAMED_SOURCES = {"shizuku", "droidify", "obtainium", "installwithoptions", "magisk"}
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_\-]+)\}")


class RecipeError(ValueError):
    pass


@dataclass
class Recipe:
    name: str
    path: Path | None = None
    description: str = ""
    rom_profile: str = ""
    devices: list[str] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.description:
            d["description"] = self.description
        if self.rom_profile:
            d["rom_profile"] = self.rom_profile
        d["devices"] = list(self.devices)
        if self.inputs:
            d["inputs"] = [dict(i) for i in self.inputs]
        d["steps"] = [dict(s) for s in self.steps]
        return d

    def dumps(self) -> str:
        return tomlw.dumps(self.to_dict())

    @property
    def flashes(self) -> bool:
        return any(s["action"] in FLASHING for s in self.steps)

    def base_dir(self) -> Path:
        return self.path.parent if self.path else hostplatform.recipes_dir()


# ---------------------------------------------------------------- loading and validation

def _line_of(text: str, section: str, index: int, key: str | None = None) -> int:
    lines = text.splitlines()
    count = -1
    start = None
    for no, line in enumerate(lines, 1):
        if line.strip() == f"[[{section}]]":
            count += 1
            if count == index:
                start = no
                if key is None:
                    return no
                continue
        if start is not None:
            if line.strip().startswith("[") and no != start:
                break
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                return no
    return start or 0


def _top_line(text: str, key: str) -> int:
    for no, line in enumerate(text.splitlines(), 1):
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            return no
    return 0


def validate(raw: dict, text: str = "", source: str = "recipe") -> Recipe:
    def err(msg: str, line: int = 0) -> RecipeError:
        return RecipeError(f"{source}{f', line {line}' if line else ''}: {msg}")

    unknown = set(raw) - TOP_KEYS
    if unknown:
        k = sorted(unknown)[0]
        raise err(f"unknown key '{k}'", _top_line(text, k))
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise err("'name' is required")
    inputs = raw.get("inputs", [])
    ids: set[str] = set()
    for i, inp in enumerate(inputs):
        bad = set(inp) - INPUT_KEYS
        if bad:
            k = sorted(bad)[0]
            raise err(f"input {i + 1}: unknown field '{k}'", _line_of(text, "inputs", i, k))
        if not inp.get("id"):
            raise err(f"input {i + 1}: 'id' is required", _line_of(text, "inputs", i))
        if inp["id"] in ids:
            raise err(f"input id '{inp['id']}' is declared twice", _line_of(text, "inputs", i, "id"))
        if inp.get("kind", "file") not in INPUT_KINDS:
            raise err(f"input {inp['id']}: kind must be one of {', '.join(sorted(INPUT_KINDS))}",
                      _line_of(text, "inputs", i, "kind"))
        ids.add(inp["id"])
    steps = raw.get("steps", [])
    if not steps:
        raise err("a recipe needs at least one [[steps]] entry")
    for i, step in enumerate(steps):
        action = step.get("action")
        if action not in ACTIONS:
            raise err(f"step {i + 1}: unknown action '{action}'", _line_of(text, "steps", i, "action"))
        required, optional, _ = ACTIONS[action]
        bad = set(step) - required - optional - {"action"}
        if bad:
            k = sorted(bad)[0]
            raise err(f"step {i + 1} ({action}): unknown field '{k}'", _line_of(text, "steps", i, k))
        missing = required - set(step)
        if missing:
            raise err(f"step {i + 1} ({action}): missing field '{sorted(missing)[0]}'",
                      _line_of(text, "steps", i))
        if action == "install_apk" and not (step.get("file") or step.get("source")):
            raise err(f"step {i + 1}: install_apk needs 'file' or 'source'", _line_of(text, "steps", i))
        if action == "install_apk" and step.get("source") and step["source"] not in NAMED_SOURCES:
            raise err(f"step {i + 1}: unknown source '{step['source']}'", _line_of(text, "steps", i, "source"))
        if action == "reboot" and step["to"] not in REBOOT_TARGETS:
            raise err(f"step {i + 1}: reboot 'to' must be one of {', '.join(REBOOT_TARGETS)}",
                      _line_of(text, "steps", i, "to"))
        if action == "wait" and step.get("until") and step["until"] not in WAIT_STATES:
            raise err(f"step {i + 1}: wait 'until' must be one of {', '.join(sorted(WAIT_STATES))}",
                      _line_of(text, "steps", i, "until"))
        if action == "debloat" and step.get("mode", "remove") not in ("remove", "disable"):
            raise err(f"step {i + 1}: debloat mode must be remove or disable", _line_of(text, "steps", i, "mode"))
        if action == "set_active_slot" and step["slot"] not in ("a", "b"):
            raise err(f"step {i + 1}: slot must be a or b", _line_of(text, "steps", i, "slot"))
        if action == "flash" and step.get("slots", "active") not in ("active", "all"):
            raise err(f"step {i + 1}: slots must be active or all", _line_of(text, "steps", i, "slots"))
        for key, value in step.items():
            if isinstance(value, str):
                for ref in _PLACEHOLDER.findall(value):
                    if ref not in ids:
                        raise err(f"step {i + 1}: '{{{ref}}}' is not a declared input",
                                  _line_of(text, "steps", i, key))
    return Recipe(name=raw["name"], description=raw.get("description", ""), rom_profile=raw.get("rom_profile", ""),
                  devices=list(raw.get("devices", [])), inputs=list(inputs), steps=list(steps))


def load(path: Path) -> Recipe:
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RecipeError(f"{path.name}: {exc}") from exc
    r = validate(raw, text, path.name)
    r.path = path
    return r


def load_all(directory: Path | None = None) -> tuple[list[Recipe], list[str]]:
    directory = directory or hostplatform.recipes_dir()
    out, errors = [], []
    if directory.is_dir():
        for f in sorted(directory.glob("*.toml")):
            try:
                out.append(load(f))
            except (RecipeError, OSError) as exc:
                errors.append(str(exc))
    return out, errors


def install_examples(force: bool = False) -> int:
    """Copy shipped recipes and lists into the user's recipes folder on first run. Never overwrites."""
    src = hostplatform.data_resource("recipes")
    dst = hostplatform.recipes_dir()
    copied = 0
    for f in src.rglob("*"):
        if f.is_file():
            target = dst / f.relative_to(src)
            if force or not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                copied += 1
    return copied


def save(recipe: Recipe, path: Path | None = None) -> Path:
    path = path or recipe.path
    if path is None:
        slug = re.sub(r"[^a-z0-9]+", "-", recipe.name.lower()).strip("-") or "recipe"
        path = hostplatform.recipes_dir() / f"{slug}.toml"
    text = recipe.dumps()
    validate(tomllib.loads(text), text, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    recipe.path = path
    return path


# ---------------------------------------------------------------- execution

class Callbacks(Protocol):
    def wait(self, message: str, until: str | None) -> bool: ...
    def step(self, index: int, text: str) -> None: ...


@dataclass
class Context:
    recipe: Recipe
    inputs: dict[str, str]
    ops: DeviceOps
    profile: object | None = None
    protected: set[str] = field(default_factory=set)
    state: object | None = None       # DeviceState
    model: str = ""
    settings: object | None = None
    callbacks: Callbacks | None = None
    log: Callable[[str], None] = print
    extracted: dict[str, Path] = field(default_factory=dict)
    verified: set[str] = field(default_factory=set)    # input ids whose hash or signature passed precheck

    def relock_ok(self, value: str, kind: str) -> bool:
        m = _PLACEHOLDER.fullmatch(value.strip())
        verified = bool(m and m.group(1) in self.verified)
        return profiles.relock_safe(self.profile, kind, verified)

    def sub(self, value):
        if not isinstance(value, str):
            return value
        return _PLACEHOLDER.sub(lambda m: self.inputs.get(m.group(1), m.group(0)), value)

    def path(self, value: str) -> str:
        """Input placeholders pass through untouched; literal relative paths are relative to the recipe file."""
        v = self.sub(value)
        if _PLACEHOLDER.search(value) or Path(v).is_absolute():
            return v
        return str(self.recipe.base_dir() / v)


def _ok(r) -> tuple[bool, str]:
    return (r.ok, "" if r.ok else (r.err or r.out or f"exit code {r.code}").strip()[-600:])


def run_step(ctx: Context, index: int, step: dict) -> tuple[bool, str]:
    ops = ctx.ops
    ops.step_no = index
    a = step["action"]
    if a == "reboot":
        return _ok(ops.reboot(step["to"]))
    if a == "flash":
        if ctx.state is not None and not ops.planning:
            ctx.state.record_partial_flash(f"{step['partition']} from a recipe")
        return _ok(ops.flash(step["partition"], ctx.path(step["image"]), step.get("slots", "active"),
                             bool(step.get("disable_verity"))))
    if a == "boot_image":
        return _ok(ops.boot_image(ctx.path(step["image"])))
    if a == "sideload":
        r = ops.sideload(ctx.path(step["file"]), bool(step.get("accept_unsigned")))
        if r.ok and ctx.state is not None and not ops.planning and ctx.profile is not None \
                and not step.get("accept_unsigned"):
            kind = packages.detect(Path(ctx.path(step["file"]))).kind
            ctx.state.record_flash(ctx.profile.id, ctx.profile.kind, ctx.relock_ok(step["file"], kind),
                                   Path(ctx.sub(step["file"])).name)
        return _ok(r)
    if a == "flash_all":
        pkg_path = Path(ctx.path(step["package"]))
        if ops.planning:
            ops.note(f"Extract {pkg_path.name} into the cache folder")
            script_dir = f"<extracted {pkg_path.name}>"
        else:
            if pkg_path.is_dir():
                found = find_script_dir(pkg_path)
            else:
                dest = hostplatform.cache_dir() / "extracted" / pkg_path.stem
                ctx.log(f"# Extracting {pkg_path.name}")
                extract_zip_to(pkg_path, dest)
                found = find_script_dir(dest)
            if not found:
                return False, f"No {hostplatform.flash_all_script_name()} in {pkg_path.name}"
            script_dir = str(found)
        r = ops.flash_all(script_dir, bool(step.get("keep_data")))
        if r.ok and ctx.state is not None and not ops.planning and ctx.profile is not None:
            ctx.state.record_flash(ctx.profile.id, ctx.profile.kind,
                                   ctx.relock_ok(step["package"], packages.FACTORY), pkg_path.name)
        return _ok(r)
    if a == "fastboot_update":
        r = ops.fastboot_update(ctx.path(step["package"]), bool(step.get("wipe")))
        if r.ok and ctx.state is not None and not ops.planning and ctx.profile is not None:
            ctx.state.record_flash(ctx.profile.id, ctx.profile.kind,
                                   ctx.relock_ok(step["package"], packages.FASTBOOT_UPDATE),
                                   Path(ctx.sub(step["package"])).name)
        return _ok(r)
    if a == "install_apk":
        opts = dict(reinstall=bool(step.get("reinstall", True)), downgrade=bool(step.get("downgrade")),
                    grant=bool(step.get("grant")), bypass_low_sdk=bool(step.get("bypass_low_sdk")))
        if step.get("source"):
            src = step["source"]
            if ops.planning:
                ops.note(f"Download the current {src} release (hash checked where the project publishes one)")
                return _ok(ops.install([f"<{src} APK>"], **opts))
            fetched = downloads.fetch_source(src, hostplatform.cache_dir() / "apks", hostplatform.cache_dir(),
                                             ops.abi())
            ctx.log(f"# {src} {fetched.version}: {fetched.detail}")
            return _ok(ops.install([str(fetched.path)], **opts))
        return _ok(ops.install([ctx.path(step["file"])], **opts))
    if a == "push":
        return _ok(ops.push(ctx.path(step["local"]), ctx.sub(step["remote"])))
    if a == "pull":
        return _ok(ops.pull(ctx.sub(step["remote"]), ctx.path(step["local"]), bool(step.get("preserve"))))
    if a == "debloat":
        list_path = Path(ctx.path(step["list"]))
        if not list_path.is_file():
            return False, f"debloat list not found: {list_path}"
        pkgs = debloat_mod.read_list_ordered(list_path)
        s = ops.debloat(pkgs, step.get("mode", "remove"), ctx.protected, ctx.state, ctx.model)
        if not ops.planning:
            ctx.log(f"# debloat: {len(s.done)} done, {len(s.skipped)} not installed, {len(s.refused)} refused "
                    f"(do-not-touch), {len(s.failed)} failed")
            for label, items in (("not installed", s.skipped), ("refused", s.refused), ("failed", s.failed)):
                if items:
                    ctx.log(f"#   {label}: {', '.join(items)}")
        return (not s.failed, f"failed: {', '.join(s.failed)}" if s.failed else "")
    if a == "shizuku_setup":
        return shizuku_setup(ctx, step.get("client", "droidify"), bool(step.get("install_with_options")),
                             step.get("root", "auto"))
    if a == "magisk_root":
        return magisk_root(ctx, ctx.path(step["package"]) if step.get("package") else "")
    if a == "set_active_slot":
        return _ok(ops.set_active_slot(step["slot"]))
    if a == "unlock":
        return _ok(ops.unlock(ctx.sub(step.get("code", ""))))
    if a == "lock":
        if not ops.planning and ctx.state is not None:
            allowed, why = ctx.state.relock_allowed()
            if not allowed:
                return False, why
        if ops.planning:
            ops.note("Relock runs only if the relock rule allows it at this point (checked live).")
        return _ok(ops.lock())
    if a == "wait":
        until = step.get("until")
        ops.note(step["message"] + (f"  (continues when the device is in {until} mode)" if until else
                                    "  (continues when you click Continue)"), kind="wait")
        if ops.planning:
            if until:
                ops.wait_for(until)
            return True, ""
        if ctx.callbacks is None:
            return (ops.wait_for(until), "") if until else (True, "")
        if not ctx.callbacks.wait(step["message"], until):
            return False, "stopped while waiting"
        return True, ""
    if a == "note":
        ops.note(step["message"])
        return True, ""
    raise OpError(f"unhandled action {a}")


def shizuku_setup(ctx: Context, client: str, iwo: bool, root: str) -> tuple[bool, str]:
    ops = ctx.ops
    from .ops import SHIZUKU_PKG
    if ops.planning:
        ops.note("If Shizuku is missing: open its Play listing on the phone (when Play is present) or install the "
                 "latest APK from GitHub")
    elif not ops.package_installed(SHIZUKU_PKG):
        if ops.has_play():
            ops.open_play_listing(SHIZUKU_PKG)
            ctx.log("# Shizuku's Play listing is open on the phone. Tap Install there; this step continues by "
                    "itself once it is installed (up to five minutes).")
            if not ops.wait_for_package(SHIZUKU_PKG, timeout=300):
                return False, "Shizuku was not installed from the Play listing within five minutes"
        else:
            fetched = downloads.fetch_source("shizuku", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
            ctx.log(f"# Shizuku {fetched.version}: {fetched.detail}")
            ok, msg = _ok(ops.install([str(fetched.path)], reinstall=True))
            if not ok:
                return ok, msg
    srcs = load_named_sources()
    for key in [client] + (["installwithoptions"] if iwo else []):
        names = downloads.package_names(srcs.get(key, {}))
        if ops.planning:
            ops.note(f"Install {key} if it is not installed")
            ops.install([f"<{key} APK>"], reinstall=True)
            continue
        have = ops.installed_variant(names)
        if have:
            ctx.log(f"# {key} is already installed ({have})")
            continue
        fetched = downloads.fetch_source(key, hostplatform.cache_dir() / "apks", hostplatform.cache_dir(), ops.abi())
        ctx.log(f"# {key} {fetched.version}: {fetched.detail}")
        ok, msg = _ok(ops.install([str(fetched.path)], reinstall=True))
        if not ok:
            return ok, msg
    use_root = ops.root_available() if root == "auto" else bool(root is True or root == "yes")
    r = ops.shizuku_start(root=use_root)
    ok, msg = _ok(r)
    from .shizukuguide import connect_steps
    ops.note("On the phone: " + connect_steps(client))
    if not use_root:
        ops.note("Shizuku stops on reboot without root. Restart it from the cable, or from Shizuku's own app over "
                 "Wireless Debugging (Android 11+).")
    return ok, msg


def magisk_root(ctx: Context, package_path: str) -> tuple[bool, str]:
    """Recipe form of the Root section: needs the phone booted with adb to start."""
    from . import magisk
    ops = ctx.ops
    if ops.planning:
        ops.note("Read ro.build.fingerprint, ro.build.id and the active slot from the phone")
        ops.reboot("bootloader")
        ops.note("Read has-slot:init_boot; it must agree with the package")
        ops.reboot("system")
        ops.note("Extract init_boot.img (or boot.img) matching the running build; install Magisk if missing")
        ops.push("<stock image>", "/sdcard/Download/")
        ops.note("Patch it in the Magisk app: Install, Select and patch a file", kind="wait")
        ops.pull("/sdcard/Download/magisk_patched-<id>.img", "<device folder>")
        ops.reboot("bootloader")
        ops.flash("<init_boot or boot>", "<magisk_patched image>")
        return True, ""
    props = ops.getprop()
    build = magisk.BuildInfo.from_props(props)
    if not build.build_id:
        return False, "Could not read the running build. The phone must be booted with adb working."
    pkg = packages.detect(Path(package_path)) if package_path else None
    rooted = ops.root_available()
    r = ops.reboot("bootloader")
    if not r.ok:
        return _ok(r)
    part, why = magisk.choose_partition(ops.getvar("has-slot:init_boot"),
                                        pkg.has_init_boot if (pkg and pkg.kind == packages.FACTORY) else None)
    ctx.log(f"# {why}")
    if not part:
        return False, why
    r = ops.reboot("system")
    if not r.ok:
        return _ok(r)
    stock = magisk.stock_path(ops.serial, build.build_id, part)
    stock.parent.mkdir(parents=True, exist_ok=True)
    if rooted:
        r = ops.read_partition_as_root(part, build.slot_suffix, str(stock))
        if not r.ok:
            return _ok(r)
    elif pkg and pkg.kind == packages.FACTORY:
        if not packages.build_matches(pkg.build, build.build_id, build.incremental):
            return False, (f"The factory image is build {pkg.build}; the phone runs {build.build_id}. Download "
                           f"the image for {build.build_id}.")
        packages.extract_from_factory(pkg, part, stock)
    elif pkg and pkg.kind == packages.OTA_PAYLOAD:
        fp = pkg.metadata.get("post-build", "")
        if fp and fp != build.fingerprint:
            return False, "The ROM zip's post-build fingerprint does not match the running build."
        if not ops.runner.tools.available("payload-dumper"):
            return False, "payload-dumper-go is needed to extract one partition from payload.bin."
        out_dir = stock.parent / "payload"
        res = ops.runner.run_blocking(Command("payload-dumper", ["-p", part, "-o", str(out_dir), package_path]))
        if not res.ok or not (out_dir / f"{part}.img").is_file():
            return False, "payload-dumper did not produce the image"
        (out_dir / f"{part}.img").replace(stock)
    else:
        return False, "No way to get the stock image: the phone is not rooted and no matching package was given."
    if not ops.package_installed("com.topjohnwu.magisk"):
        fetched = downloads.fetch_source("magisk", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
        ok, msg = _ok(ops.install([str(fetched.path)], reinstall=True))
        if not ok:
            return ok, msg
    before = set(ops.list_download_dir())
    r = ops.push(str(stock), "/sdcard/Download/")
    if not r.ok:
        return _ok(r)
    if ctx.callbacks and not ctx.callbacks.wait(
            "In the Magisk app: Install, Select and patch a file, pick the image in Download. Click Continue "
            "when Magisk says it is done.", None):
        return False, "stopped while waiting for Magisk"
    new = [n for n in ops.list_download_dir() if n not in before]
    name = magisk.newest_patched(new) or magisk.newest_patched(ops.list_download_dir())
    if not name:
        return False, "No magisk_patched-*.img found in /sdcard/Download"
    patched = magisk.patched_path(ops.serial, build.build_id, part)
    r = ops.pull(f"/sdcard/Download/{name}", str(patched))
    if not r.ok:
        return _ok(r)
    chk = magisk.check_patched(patched, stock)
    ctx.log(f"# {chk.message}")
    if not chk.ok:
        return False, chk.message
    r = ops.reboot("bootloader")
    if not r.ok:
        return _ok(r)
    r = ops.flash(part, str(patched), "active")
    if r.ok and ctx.state is not None:
        ctx.state.record_magisk_flash()
    magisk.cleanup_old_builds(ops.serial)
    return _ok(r)


def load_named_sources() -> dict:
    try:
        return downloads.load_sources()
    except OSError:
        return {}


# ---------------------------------------------------------------- planning and prechecks

def plan(recipe: Recipe, inputs: dict[str, str], runner, serial: str, protected: set[str] | None = None,
         profile=None, start_mode: str = "device") -> list:
    ops = DeviceOps(runner, serial, planning=True, mode_hint=start_mode)
    ctx = Context(recipe, {k: v for k, v in inputs.items() if v}, ops, profile, protected or set())
    for i, step in enumerate(recipe.steps):
        run_step(ctx, i, step)
    return ops.plan


@dataclass
class Precheck:
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified: set[str] = field(default_factory=set)
    passed: list[str] = field(default_factory=list)


def precheck(recipe: Recipe, inputs: dict[str, str], hashes: dict[str, str], ops: DeviceOps, profile,
             codename: str, battery: int | None, threshold: int, lock_state: str,
             battery_override: bool = False, verify_signature: Callable[[str, str, str], tuple[bool, str]] | None = None,
             fastboot_version: tuple[int, ...] | None = None) -> Precheck:
    """Check every input before the first step runs. Any problem means nothing runs."""
    pc = Precheck(True)
    for inp in recipe.inputs:
        val = inputs.get(inp["id"], "")
        if not val:
            if not inp.get("optional"):
                pc.problems.append(f"{inp.get('label') or inp['id']}: no file chosen")
            continue
        p = Path(val)
        if not p.exists():
            pc.problems.append(f"{inp['id']}: {val} does not exist")
            continue
        want = hashes.get(inp["id"], "")
        if want:
            got = downloads.sha256_file(p) if p.is_file() else ""
            if got != downloads.normalize_hash(want):
                pc.problems.append(f"{inp['id']}: SHA-256 does not match the published hash")
            else:
                pc.verified.add(inp["id"])
                pc.passed.append(f"{inp['id']}: SHA-256 matches")
        elif inp.get("hash"):
            pc.warnings.append(f"{inp['id']}: no published hash entered; the file is unverified")
        kind = inp.get("kind", "file")
        if kind in ("package", "file") and (p.is_dir() or p.suffix.lower() in (".zip", ".img")):
            info = packages.detect(p)
            if info.kind != packages.UNKNOWN:
                m = packages.match_device(info, codename)
                if m.blocked:
                    pc.problems.append(f"{inp['id']}: {m.message}")
                elif m.checked_by != "metadata":
                    pc.warnings.append(f"{inp['id']}: {m.message}")
                pc.warnings += [f"{inp['id']}: {w}" for w in m.warnings]
    min_fb = getattr(profile, "min_fastboot", "") if profile is not None else ""
    if min_fb and recipe.flashes and not parsers.version_at_least(fastboot_version, min_fb):
        pc.problems.append(f"This ROM needs fastboot {min_fb} or newer; found {parsers.version_str(fastboot_version)}. "
                           "Fetch the current platform-tools in Settings.")
    if recipe.devices and codename and codename.lower() not in {d.lower() for d in recipe.devices}:
        pc.problems.append(f"This recipe is for {', '.join(recipe.devices)}; the device is {codename}.")
    if recipe.flashes:
        if battery is not None and battery < threshold and not battery_override:
            pc.problems.append(f"Battery is at {battery}%, below the {threshold}% flash threshold.")
        actions = [s["action"] for s in recipe.steps]
        if lock_state == "locked" and "unlock" not in actions and any(
                a in actions for a in ("flash", "flash_all", "fastboot_update", "boot_image", "magisk_root")):
            pc.problems.append("The bootloader is locked and the recipe has no unlock step.")
    if profile is not None and getattr(profile, "verify", "") == "ssh-keygen" and verify_signature:
        sig = next((inputs.get(i["id"]) for i in recipe.inputs if i.get("kind") == "signature"), "")
        signers = next((inputs.get(i["id"]) for i in recipe.inputs if i.get("kind") == "allowed_signers"), "")
        target_id = next((i["id"] for i in recipe.inputs if i.get("kind") == "package"), "")
        target = inputs.get(target_id, "") if target_id else ""
        if sig and signers and target:
            ok, msg = verify_signature(target, sig, signers)
            (pc.passed if ok else pc.problems).append(msg)
            if ok:
                pc.verified.add(target_id)
        else:
            pc.warnings.append("Signature files not supplied; the package is unverified.")
    pc.ok = not pc.problems
    return pc
