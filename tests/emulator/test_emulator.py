"""ADB paths run against a real Android emulator. CI only: needs DEADBOOT_EMULATOR=1,
ANDROID_SERIAL and DEADBOOT_TEST_APKS (from build_test_apks.sh)."""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.skipif(os.environ.get("DEADBOOT_EMULATOR") != "1", reason="emulator tests run in CI")

from deadboot import debloat, downloads, hostplatform, parsers, recipes  # noqa: E402
from deadboot.actionlog import ActionLog  # noqa: E402
from deadboot.devicestate import DeviceState  # noqa: E402
from deadboot.ops import SHIZUKU_PKG, DeviceOps  # noqa: E402
from deadboot.runner import Command, Runner  # noqa: E402
from deadboot.settings import Settings  # noqa: E402
from deadboot.tools import Tools  # noqa: E402

SERIAL = os.environ.get("ANDROID_SERIAL", "emulator-5554")
APKS = Path(os.environ.get("DEADBOOT_TEST_APKS", "test-apks"))
TEST_PKG = "dev.deadboot.testapp"
OLD_PKG = "dev.deadboot.oldapp"
SYSTEM_CANDIDATES = ("com.android.deskclock", "com.android.calculator2", "com.android.camera2",
                     "com.android.gallery3d", "com.android.music", "com.android.soundrecorder",
                     "com.android.wallpaper.livepicker", "com.android.dreams.basic", "com.android.egg")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication(sys.argv[:1])


@pytest.fixture(scope="module")
def runner(qapp):
    tools = Tools(Settings())
    assert tools.available("adb"), "adb must be on PATH"
    return Runner(tools, ActionLog())


@pytest.fixture
def ops(runner):
    return DeviceOps(runner, SERIAL)


def test_device_detection(runner):
    r = runner.run_blocking(Command("adb", ["devices", "-l"]), quiet=True)
    devs = {d.serial: d for d in parsers.parse_adb_devices(r.out)}
    assert SERIAL in devs and devs[SERIAL].state == "device"


def test_props_and_battery(ops):
    props = ops.getprop()
    assert props.get("ro.build.version.sdk", "").isdigit()
    assert ops.battery() is not None
    assert ops.state() == "device"


def test_install_and_uninstall_single(ops):
    ops.uninstall(TEST_PKG, keep_data=False)
    r = ops.install([str(APKS / "base.apk")])
    assert r.ok, r.text
    assert ops.package_installed(TEST_PKG)
    r = ops.uninstall(TEST_PKG, keep_data=False)
    assert r.ok and not ops.package_installed(TEST_PKG)


def test_install_split(ops):
    r = ops.install([str(APKS / "base.apk"), str(APKS / "split.apk")], reinstall=True)
    assert r.ok, r.text
    paths = ops.apk_paths(TEST_PKG)
    assert len(paths) == 2, paths
    ops.uninstall(TEST_PKG, keep_data=False)


def test_low_target_sdk_bypass(ops):
    sdk = int(ops.prop("ro.build.version.sdk") or 0)
    if sdk < 34:
        pytest.skip("the low-target-SDK block exists on Android 14+")
    ops.uninstall(OLD_PKG, keep_data=False)
    r = ops.install([str(APKS / "oldsdk.apk")])
    fail = parsers.decode_install_failure(r.text)
    assert fail is not None and fail.suggestion == "bypass_low_sdk", r.text
    r = ops.install([str(APKS / "oldsdk.apk")], bypass_low_sdk=True)
    assert r.ok and parsers.decode_install_failure(r.text) is None, r.text
    ops.uninstall(OLD_PKG, keep_data=False)


def test_push_pull(ops, tmp_path):
    src = tmp_path / "hello file.txt"
    src.write_bytes(os.urandom(200_000))
    assert ops.push(str(src), "/sdcard/Download/deadboot-test.txt").ok
    back = tmp_path / "back.txt"
    assert ops.pull("/sdcard/Download/deadboot-test.txt", str(back), preserve=True).ok
    assert back.read_bytes() == src.read_bytes()
    ops.shell("rm -f /sdcard/Download/deadboot-test.txt", writes=True)


def test_debloat_disable_and_restore(ops, tmp_path):
    assert ops.install([str(APKS / "base.apk")], reinstall=True).ok
    st = DeviceState(SERIAL, base=tmp_path)
    s = ops.debloat([TEST_PKG, "com.android.systemui"], "disable", debloat.load_protected(), st, "emu")
    assert s.done == [TEST_PKG] and s.refused == ["com.android.systemui"]
    assert TEST_PKG in parsers.parse_package_names(ops.qshell("pm list packages -d"))
    assert ops.restore_package(TEST_PKG, st.debloat_record()[TEST_PKG]["mode"]).ok
    assert TEST_PKG not in parsers.parse_package_names(ops.qshell("pm list packages -d"))
    ops.uninstall(TEST_PKG, keep_data=False)


def test_debloat_remove_and_restore_system_app(ops, tmp_path):
    system = parsers.parse_package_names(ops.qshell("pm list packages -s"))
    target = next((p for p in SYSTEM_CANDIDATES if p in system), None)
    if target is None:
        pytest.skip("no harmless system app on this image")
    st = DeviceState(SERIAL, base=tmp_path)
    s = ops.debloat([target], "remove", debloat.load_protected(), st, "emu")
    assert s.done == [target], s
    assert target not in ops.installed_package_names(include_disabled=False)
    r = ops.restore_package(target, "remove")
    assert r.ok, r.text
    assert target in ops.installed_package_names(include_disabled=False)


def test_logcat_capture(runner, qapp):
    r = runner.run_blocking(Command("adb", ["logcat", "-d", "-t", "50"], SERIAL), quiet=True)
    assert r.ok and r.out.strip()
    lines = []
    proc = runner.start(Command("adb", ["logcat", "-v", "threadtime", "*:V"], SERIAL), on_line=lines.append)
    runner.run_blocking(Command("adb", ["shell", "log -t deadboot-test hello-from-ci"], SERIAL), quiet=True)
    end = time.time() + 15
    while time.time() < end and not any("hello-from-ci" in ln for ln in lines):
        qapp.processEvents()
        time.sleep(0.05)
    proc.kill()
    proc.waitForFinished(3000)
    assert any("hello-from-ci" in ln for ln in lines)


def test_shizuku_install_and_start(ops):
    if not ops.package_installed(SHIZUKU_PKG):
        fetched = downloads.fetch_source("shizuku", hostplatform.cache_dir() / "apks", hostplatform.cache_dir())
        r = ops.install([str(fetched.path)], reinstall=True)
        assert r.ok, r.text
    starter, how = ops.shizuku_starter()
    assert starter, how
    r = ops.shizuku_start(root=False)
    assert r.ok, r.err or r.out
    assert ops.shizuku_running()


def test_new_phone_setup_recipe_for_real(runner):
    recipes.install_examples()
    r = recipes.load(hostplatform.recipes_dir() / "new-phone-setup.toml")
    ops = DeviceOps(runner, SERIAL)
    ctx = recipes.Context(r, {}, ops, None, debloat.load_protected(), None, "emu", log=print)
    for i, step in enumerate(r.steps):
        ok, msg = recipes.run_step(ctx, i, step)
        assert ok, f"step {i + 1} {step['action']}: {msg}"
    assert ops.package_installed("com.looker.droidify")
    assert ops.shizuku_running()
