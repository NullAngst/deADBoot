import sys
import tomllib
from pathlib import Path

import pytest

from deadboot import debloat, devicestate, hostplatform, magisk, ops, tomlw, winnames
from deadboot.runner import Command
from deadboot.settings import Settings


def test_tomlw_roundtrip():
    data = {"name": 'quote " and \\ back', "flag": True, "n": 3, "items": ["a", "b"],
            "table": {"k": "v"}, "steps": [{"action": "reboot", "to": "bootloader"}, {"action": "note",
                                                                                        "message": "line\nbreak"}]}
    assert tomllib.loads(tomlw.dumps(data)) == data


def test_settings_roundtrip():
    s = Settings()
    s.theme = "verdigris"
    s.battery_threshold = 25
    s.save()
    again = Settings.load()
    assert again.theme == "verdigris" and again.battery_threshold == 25


def test_winnames_rules(tmp_path):
    assert winnames.sanitize_component('a:b*c?"d<e>f|g') == "a_b_c__d_e_f_g"
    assert winnames.sanitize_component("trailing. ") == "trailing__"
    assert winnames.sanitize_component("CON") == "CON_"
    assert winnames.sanitize_component("nul.txt") == "nul_.txt"
    assert winnames.sanitize_component("COM1.log") == "COM1_.log"
    m = winnames.NameMapper()
    assert m.map("DCIM/IMG_01.jpg") == "DCIM/IMG_01.jpg"
    assert m.map("DCIM/img_01.jpg") == "DCIM/img_01 (1).jpg"
    assert m.map("Odd:Dir/x?.txt") == "Odd_Dir/x_.txt"
    assert m.map("Odd:Dir/y.txt") == "Odd_Dir/y.txt"
    assert len(m.renames) == 3
    report = m.write_report(tmp_path, "/sdcard")
    assert "/sdcard/DCIM/img_01.jpg\tDCIM/img_01 (1).jpg" in report.read_text()
    assert winnames.long_path(Path("C:/Users/x/deep")).startswith("\\\\?\\C:")


def test_strip_wipe_sh_and_bat():
    sh = "fastboot flash bootloader b.img\nfastboot -w update image-x.zip\n"
    assert ops.strip_wipe(sh) == "fastboot flash bootloader b.img\nfastboot  update image-x.zip\n"
    bat = "fastboot -w update image-x.zip\r\necho done\r\n"
    out = ops.strip_wipe(bat)
    assert "-w" not in out and out.endswith("\r\n")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only script handling")
def test_windows_bat_handling(tmp_path):
    assert hostplatform.flash_all_script_name() == "flash-all.bat"
    assert hostplatform.script_command(tmp_path / "flash-all.bat")[:2] == ["cmd", "/c"]
    assert hostplatform.rename_pulled_files()


@pytest.mark.skipif(sys.platform == "win32", reason="Linux script handling")
def test_linux_script_handling(tmp_path):
    assert hostplatform.flash_all_script_name() == "flash-all.sh"
    assert hostplatform.script_command(tmp_path / "flash-all.sh")[0] == "bash"
    assert not hostplatform.rename_pulled_files()


def test_flash_all_keepdata_copy(runner, tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    name = hostplatform.flash_all_script_name()
    (d / name).write_text("fastboot -w update image-x.zip\n")
    runner.dry_run = True
    o = ops.DeviceOps(runner, "SER")
    r = o.flash_all(str(d), keep_data=True)
    assert r.dry
    copies = list(d.glob("*.deadboot-keepdata*"))
    assert copies and "-w" not in copies[0].read_text()
    assert (d / name).read_text() == "fastboot -w update image-x.zip\n"


def test_runner_dry_run_and_display(runner):
    runner.dry_run = True
    lines = []
    runner.line.connect(lines.append)
    r = runner.run_blocking(Command("fastboot", ["flash", "boot_a", "/x y/boot.img"], "SER", writes=True))
    assert r.ok and r.dry
    assert lines == ["[dry-run] $ fastboot -s SER flash boot_a '/x y/boot.img'"]
    log = list((runner.log.directory).glob("*.log"))[0].read_text()
    assert "SER" in log and "not executed" in log


def test_relock_rule(tmp_path):
    st = devicestate.DeviceState("SER", base=tmp_path)
    assert not st.relock_allowed()[0]
    st.record_flash("grapheneos", "rom", True, "caiman-install.zip")
    assert st.relock_allowed()[0]
    st.record_magisk_flash()
    assert not st.relock_allowed()[0]
    st.record_flash("lineageos", "rom", False, "lineage.zip")
    assert not st.relock_allowed()[0]
    st.record_flash("stock", "stock", True, "factory.zip")
    st.record_partial_flash("vendor_boot")
    assert not st.relock_allowed()[0]


def test_debloat_lists(tmp_path):
    prot = debloat.load_protected()
    assert "com.android.systemui" in prot and "com.google.android.gms" in prot
    p = tmp_path / "l.txt"
    debloat.write_list(p, ["b", "a", "a"], "header")
    assert debloat.read_list_ordered(p) == ["a", "b"]
    assert debloat.component_package("com.foo/.Main") == "com.foo"
    assert debloat.matches_vendor("com.samsung.android.bixby", "Samsung")
    assert debloat.restore_cmd("x", "remove")[-2:] == ["install-existing", "x"]


def test_debloat_planning_refuses_protected(runner):
    o = ops.DeviceOps(runner, "SER", planning=True)
    s = o.debloat(["com.android.systemui", "com.facebook.appmanager"], "disable", debloat.load_protected())
    assert s.refused == ["com.android.systemui"]
    texts = [i.text for i in o.plan if i.kind == "cmd"]
    assert texts == ["adb -s SER shell pm disable-user --user 0 com.facebook.appmanager"]


def test_magisk_partition_and_patch(tmp_path):
    assert magisk.choose_partition("yes", True)[0] == "init_boot"
    assert magisk.choose_partition("no", False)[0] == "boot"
    assert magisk.choose_partition("no", None)[0] == "boot"
    assert magisk.choose_partition("yes", False)[0] is None
    assert magisk.choose_partition(None, True)[0] is None
    stock = tmp_path / "stock.img"
    stock.write_bytes(b"ANDROID!" + b"\0" * 100)
    good = tmp_path / "p.img"
    good.write_bytes(b"ANDROID!" + b"\1" * 100)
    assert magisk.check_patched(good, stock).ok
    bad = tmp_path / "bad.img"
    bad.write_bytes(b"NOTBOOT!" + b"\1" * 100)
    assert not magisk.check_patched(bad, stock).ok
    short = tmp_path / "s.img"
    short.write_bytes(b"ANDROID!")
    assert not magisk.check_patched(short, stock).ok
    assert magisk.newest_patched(["x.img", "magisk_patched-28100_abcde.img", "magisk_patched-28100_zzzzz.img"]) \
        == "magisk_patched-28100_zzzzz.img"


def test_magisk_cleanup():
    for i, b in enumerate(("B1", "B2", "B3")):
        d = magisk.build_dir("SER", b)
        d.mkdir(parents=True)
        (d / "stock-init_boot.img").write_bytes(b"x")
        import os
        os.utime(d, (1000 + i, 1000 + i))
    removed = magisk.cleanup_old_builds("SER")
    assert [r.name for r in removed] == ["B1"]
    assert magisk.latest_saved_stock("SER")[1] == "init_boot"


def test_flash_plan_marks_slot(runner):
    o = ops.DeviceOps(runner, "SER", planning=True, mode_hint="bootloader")
    o.flash("init_boot", "/p.img")
    o.flash("vbmeta", "/v.img", slots="all", disable_verity=True)
    cmds = [i.text for i in o.plan if i.kind == "cmd"]
    assert cmds[0] == "fastboot -s SER flash 'init_boot_<active slot>' /p.img"
    assert cmds[1] == ("fastboot -s SER --disable-verity --disable-verification flash --slot=all vbmeta /v.img")


def test_ab_roundtrip(tmp_path):
    import io
    import tarfile
    from deadboot import abfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"hello"
        ti = tarfile.TarInfo("apps/com.example/f/note.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    ab = abfile.make_test_ab(buf.getvalue(), tmp_path / "b.ab")
    hdr = abfile.read_header(ab)
    assert hdr.compressed and hdr.encryption == "none"
    tar = abfile.to_tar(ab, tmp_path / "b.tar")
    n = abfile.extract_tar(tar, tmp_path / "out")
    assert n == 1 and (tmp_path / "out/apps/com.example/f/note.txt").read_bytes() == b"hello"
    enc = tmp_path / "e.ab"
    enc.write_bytes(b"ANDROID BACKUP\n5\n1\nAES-256\nxxxx")
    with pytest.raises(abfile.AbError):
        abfile.to_tar(enc, tmp_path / "e.tar")


def test_runner_real_process(tmp_path):
    """run_blocking actually executes, streams lines and returns the exit code (no device needed)."""
    from deadboot.actionlog import ActionLog
    from deadboot.runner import Runner

    class PyTools:
        def path(self, name):
            return sys.executable

        def argv(self, name):
            return [sys.executable] if name == "py" else None

        def available(self, name):
            return name == "py"

    r = Runner(PyTools(), ActionLog(tmp_path / "logs"))
    seen = []
    res = r.run_blocking(Command("py", ["-c", "import sys; print('one'); print('two'); sys.exit(3)"]),
                         on_line=seen.append, quiet=True)
    assert res.code == 3 and seen == ["one", "two"] and "two" in res.out
    missing = r.run_blocking(Command("adb", ["devices"]), quiet=True)
    assert missing.code == 127
