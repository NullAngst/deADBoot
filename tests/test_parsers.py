from conftest import fixture_text

from deadboot import parsers


def test_adb_devices():
    devs = parsers.parse_adb_devices(fixture_text("adb_devices.txt"))
    by = {d.serial: d for d in devs}
    assert by["4A281FDJH00ABC"].state == "device"
    assert by["4A281FDJH00ABC"].props["device"] == "caiman"
    assert by["4A281FDJH00ABC"].props["model"] == "Pixel_9_Pro"
    assert by["R58N12ABCDE"].state == "unauthorized"
    assert by["0123456789"].state == "no-permissions"
    assert by["0123456789"].props.get("usb") == "1-4"
    assert by["192.168.1.40:41235"].state == "offline"
    assert by["ZY22ABCD"].state == "recovery"
    assert by["ZY22EFGH"].state == "sideload"
    assert len(devs) == 7


def test_fastboot_devices():
    devs = parsers.parse_fastboot_devices(fixture_text("fastboot_devices.txt"))
    assert [d.serial for d in devs] == ["4A281FDJH00ABC", "9B111FDJH00XYZ"]
    assert all(d.state == "bootloader" for d in devs)
    assert parsers.parse_fastboot_devices("no permissions; see [http://...]\tfastboot\n")[0].state == "no-permissions"


def test_getprop():
    p = parsers.parse_getprop(fixture_text("getprop.txt"))
    assert p["ro.build.id"] == "AP4A.250105.002"
    assert p["ro.boot.slot_suffix"] == "_a"
    assert p["persist.sys.multi.line"] == "first\nsecond"
    assert p["ro.empty"] == ""


def test_getvar_all():
    v = parsers.parse_getvar(fixture_text("getvar_all.txt"))
    assert v["unlocked"] == "yes"
    assert v["current-slot"] == "a"
    assert v["has-slot:init_boot"] == "yes"
    assert v["partition-size:init_boot_a"] == "0x800000"
    assert v["product"] == "caiman"
    assert v["version-bootloader"] == "ripcurrentpro-15.1-12451543"
    assert "all" not in v


def test_getvar_single():
    assert parsers.parse_getvar(fixture_text("getvar_single.txt"))["current-slot"] == "b"


def test_install_failures():
    f = parsers.decode_install_failure(
        "Performing Streamed Install\nadb: failed to install x.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: "
        "Existing package com.x signatures do not match newer version; ignoring!]")
    assert f.code == "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
    assert f.suggestion == "uninstall_reinstall"
    f = parsers.decode_install_failure(
        "Failure [INSTALL_FAILED_DEPRECATED_SDK_VERSION: App package must target at least SDK version 23, "
        "but found 17]")
    assert f.suggestion == "bypass_low_sdk"
    assert parsers.decode_install_failure("Failure [INSTALL_FAILED_VERSION_DOWNGRADE]").suggestion == "downgrade"
    assert parsers.decode_install_failure("Success") is None
    unknown = parsers.decode_install_failure("Failure [INSTALL_FAILED_SOMETHING_NEW]")
    assert unknown.code == "INSTALL_FAILED_SOMETHING_NEW"


def test_ls_la():
    entries = {e.name: e for e in parsers.parse_ls_la(fixture_text("ls_la.txt"))}
    assert "." not in entries and ".." not in entries
    assert entries["DCIM"].is_dir
    assert entries["notes with spaces.txt"].size == 104857
    assert entries["sdcard"].link_target == "/storage/self/primary"
    assert entries["null"].size == 0


def test_pm_list():
    pk = {p.name: p for p in parsers.parse_pm_list(fixture_text("pm_list.txt"))}
    assert pk["com.looker.droidify"].path.endswith("base.apk")
    assert pk["com.looker.droidify"].version_code == "660"
    assert pk["com.example.plain"].installer == "com.android.vending"
    assert pk["com.android.settings"].installer == ""


def test_battery_and_df():
    assert parsers.battery_level(fixture_text("battery.txt")) == 84
    df = parsers.parse_df("Filesystem 1K-blocks Used Available Use% Mounted on\n"
                          "/dev/block/dm-48 236000000 54000000 182000000 23% /data\n")
    assert df == {"size": 236000000, "used": 54000000, "avail": 182000000}


def test_versions():
    assert parsers.parse_fastboot_version("fastboot version 35.0.2-12147458\nInstalled as /x") == (35, 0, 2)
    assert parsers.parse_adb_version("Android Debug Bridge version 1.0.41\nVersion 35.0.2-12147458\n") == (35, 0, 2)
    assert parsers.version_at_least((35, 0, 2), "35.0.1")
    assert not parsers.version_at_least((34, 0, 5), "35.0.1")
    assert not parsers.version_at_least(None, "35.0.1")


def test_mdns_and_build_date():
    s = parsers.parse_mdns_services("List of discovered mdns services\n"
                                    "adb-4A28-abc\t_adb-tls-connect._tcp\t192.168.1.40:37811\n")
    assert s[0].address == "192.168.1.40:37811"
    assert parsers.build_id_date("AP4A.250105.002") == "250105"


def test_find_stat():
    rows = parsers.parse_find_stat("1024 1736500000 /sdcard/DCIM/a b.jpg\nfind: x: Permission denied\n")
    assert rows == [(1024, 1736500000, "/sdcard/DCIM/a b.jpg")]
