import tomllib

import pytest

from deadboot import hostplatform, profiles, recipes


def test_shipped_profiles_load():
    profs, errors = profiles.load_all()
    assert not errors
    assert {"stock", "grapheneos", "lineageos", "generic"} <= set(profs)
    g = profs["grapheneos"]
    assert g.relock_supported and not g.root_supported and g.min_fastboot == "35.0.1"
    assert g.url("downloads", "caiman") == "https://grapheneos.org/releases#caiman-stable"
    assert profiles.max_min_fastboot(profs) == "35.0.1"


def test_profile_validation():
    with pytest.raises(profiles.ProfileError):
        profiles.validate({"id": "x", "name": "X", "verfy": "sha256"}, "x.toml")
    with pytest.raises(profiles.ProfileError):
        profiles.validate({"id": "x", "name": "X", "verify": "ssh-keygen"}, "x.toml")
    with pytest.raises(profiles.ProfileError):
        profiles.validate({"id": "x", "name": "X", "package_types": ["zipfile"]}, "x.toml")


def shipped():
    out = {}
    for f in sorted(hostplatform.data_resource("recipes").glob("*.toml")):
        out[f.stem] = recipes.load(f)
    return out


def test_shipped_recipes_validate():
    r = shipped()
    assert {"lineageos-mindthegapps", "lineageos-microg", "grapheneos-pixel", "new-phone-setup"} <= set(r)


def _plan_text(items):
    return [i.text for i in items if i.kind == "cmd"]


def test_lineage_plan(runner):
    r = shipped()["lineageos-mindthegapps"]
    items = recipes.plan(r, {"recovery": "/f/recovery.img", "rom": "/f/lineage.zip", "gapps": "/f/mtg.zip"},
                         runner, "SER1")
    cmds = _plan_text(items)
    assert cmds == [
        "adb -s SER1 reboot bootloader",
        "fastboot -s SER1 boot /f/recovery.img",
        "adb -s SER1 sideload /f/lineage.zip",
        "adb -s SER1 reboot recovery",
        "adb -s SER1 sideload /f/mtg.zip",
        "adb -s SER1 reboot",
    ]
    assert any(i.kind == "wait" for i in items)
    assert all(i.writes for i in items if i.kind == "cmd")


def test_grapheneos_plan(runner):
    r = shipped()["grapheneos-pixel"]
    cmds = _plan_text(recipes.plan(r, {"package": "/f/caiman-install.zip", "signature": "/f/s.sig",
                                       "signers": "/f/allowed_signers"}, runner, "SER1"))
    assert cmds[0] == "adb -s SER1 reboot bootloader"
    assert cmds[1] == "fastboot -s SER1 flashing unlock"
    assert cmds[2].startswith("ANDROID_SERIAL=SER1 ")
    assert hostplatform.flash_all_script_name() in cmds[2]
    assert cmds[3] == "fastboot -s SER1 flashing lock"
    assert cmds[4] == "fastboot -s SER1 reboot"


def test_new_phone_plan(runner):
    r = shipped()["new-phone-setup"]
    items = recipes.plan(r, {}, runner, "SER1", protected={"com.facebook.system"})
    cmds = _plan_text(items)
    assert "adb -s SER1 shell pm uninstall -k --user 0 com.facebook.appmanager" in cmds
    assert not any("com.facebook.system" in c for c in cmds)
    assert any("refused" in i.text and "com.facebook.system" in i.text for i in items)
    assert any(c.startswith("adb -s SER1 install -r") for c in cmds)


def test_recipe_errors_name_the_line():
    text = 'name = "x"\n\n[[steps]]\naction = "reboot"\nto = "bootloader"\n\n[[steps]]\naction = "flsh"\n'
    with pytest.raises(recipes.RecipeError, match="line 8"):
        recipes.validate(tomllib.loads(text), text, "x.toml")
    text = 'name = "x"\n\n[[steps]]\naction = "reboot"\ntoo = "bootloader"\n'
    with pytest.raises(recipes.RecipeError, match="line 5.*'too'"):
        recipes.validate(tomllib.loads(text), text, "x.toml")
    text = 'name = "x"\n\n[[steps]]\naction = "sideload"\nfile = "{rom}"\n'
    with pytest.raises(recipes.RecipeError, match="not a declared input"):
        recipes.validate(tomllib.loads(text), text, "x.toml")


def test_recipe_roundtrip(tmp_path):
    r = shipped()["lineageos-mindthegapps"]
    p = recipes.save(r, tmp_path / "copy.toml")
    again = recipes.load(p)
    assert again.steps == r.steps and again.inputs == r.inputs and again.name == r.name


def test_install_examples():
    assert recipes.install_examples() >= 5
    assert recipes.install_examples() == 0
    assert (hostplatform.recipes_dir() / "lists" / "my-bloat.txt").is_file()


def test_precheck_blocks_wrong_device(runner, payload_zip, tmp_path):
    from deadboot.ops import DeviceOps
    r = shipped()["lineageos-microg"]
    rec = tmp_path / "recovery.img"
    rec.write_bytes(b"ANDROID!")
    ops = DeviceOps(runner, "SER1", planning=True)
    pc = recipes.precheck(r, {"recovery": str(rec), "rom": str(payload_zip)}, {}, ops, None, "caiman",
                          80, 30, "unlocked")
    assert not pc.ok and any("komodo" in p for p in pc.problems)
    pc = recipes.precheck(r, {"recovery": str(rec), "rom": str(payload_zip)}, {}, ops, None, "komodo",
                          10, 30, "unlocked")
    assert not pc.ok and any("Battery" in p for p in pc.problems)
    pc = recipes.precheck(r, {"recovery": str(rec), "rom": str(payload_zip)}, {"rom": "0" * 64}, ops, None,
                          "komodo", 80, 30, "unlocked")
    assert any("SHA-256" in p for p in pc.problems)


def test_relock_safe_rules():
    from deadboot import packages
    profs, _ = profiles.load_all()
    g, lin = profs["grapheneos"], profs["lineageos"]
    assert profiles.relock_safe(g, packages.FACTORY, verified=True)
    assert not profiles.relock_safe(g, packages.FACTORY, verified=False)
    assert not profiles.relock_safe(g, packages.RECOVERY_ZIP, verified=True)   # wrong profile picked
    assert not profiles.relock_safe(lin, packages.RECOVERY_ZIP, verified=True)
    assert not profiles.relock_safe(None, packages.FACTORY, verified=True)


def test_precheck_passing_hash(runner, payload_zip, tmp_path):
    from deadboot import downloads
    from deadboot.ops import DeviceOps
    r = shipped()["lineageos-microg"]
    rec = tmp_path / "recovery.img"
    rec.write_bytes(b"ANDROID!")
    ops = DeviceOps(runner, "SER1", planning=True)
    good = downloads.sha256_file(payload_zip)
    pc = recipes.precheck(r, {"recovery": str(rec), "rom": str(payload_zip)}, {"rom": good}, ops, None,
                          "komodo", 80, 30, "unlocked")
    assert pc.ok and pc.verified == {"rom"} and pc.passed
