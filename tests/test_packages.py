from deadboot import packages


def test_factory(factory_zip, tmp_path):
    info = packages.detect(factory_zip)
    assert info.kind == packages.FACTORY
    assert info.build == "ap4a.250105.002"
    assert info.android_info["board"] == ["caiman"]
    assert info.has_init_boot
    m = packages.match_device(info, "caiman", bootloader_version="ripcurrentpro-15.1-12451543")
    assert m.ok and m.checked_by == "metadata" and not m.warnings
    m = packages.match_device(info, "komodo")
    assert m.blocked
    m = packages.match_device(info, "caiman", bootloader_version="ripcurrentpro-14.9-1")
    assert m.ok and m.warnings
    out = packages.extract_from_factory(info, "init_boot", tmp_path / "stock.img")
    assert out.read_bytes().startswith(b"ANDROID!")
    assert packages.build_matches(info.build, "AP4A.250105.002")
    assert not packages.build_matches(info.build, "AP4A.250205.001")


def test_rollback(factory_zip):
    info = packages.detect(factory_zip)
    assert packages.rollback_warning(info, "BP1A.250305.019")
    assert not packages.rollback_warning(info, "AP4A.241205.013")


def test_recovery_zip(recovery_zip):
    info = packages.detect(recovery_zip)
    assert info.kind == packages.RECOVERY_ZIP
    assert info.asserted_devices == ["caiman"]
    assert packages.match_device(info, "caiman").ok
    assert packages.match_device(info, "tokay").blocked


def test_payload_zip(payload_zip):
    info = packages.detect(payload_zip)
    assert info.kind == packages.OTA_PAYLOAD
    assert info.target_devices == ["komodo"]
    assert packages.match_device(info, "caiman").blocked
    assert packages.match_device(info, "", fastboot_product="komodo").ok
    assert packages.rollback_warning(info, "", "1736600000")


def test_loose_and_fastboot_update(nometa_zip, fastboot_update_zip, tmp_path):
    info = packages.detect(nometa_zip)
    assert info.kind == packages.LOOSE_IMAGES
    m = packages.match_device(info, "caiman")
    assert m.ok and m.checked_by == "nothing"
    img = tmp_path / "lineage-22.1-recovery-caiman.img"
    img.write_bytes(b"ANDROID!")
    m = packages.match_device(packages.detect(img), "caiman")
    assert m.checked_by == "filename"
    fu = packages.detect(fastboot_update_zip)
    assert fu.kind == packages.FASTBOOT_UPDATE
    assert packages.match_device(fu, "komodo").ok


def test_folder_factory(factory_zip, tmp_path):
    from deadboot.ops import extract_zip_to
    folder = extract_zip_to(factory_zip, tmp_path / "ex")
    info = packages.detect(folder)
    assert info.kind == packages.FACTORY
    assert info.android_info["board"] == ["caiman"]
    out = packages.extract_from_factory(info, "boot", tmp_path / "b.img")
    assert out.read_bytes().startswith(b"ANDROID!")


def test_unknown(make_zip):
    assert packages.detect(make_zip("random.zip", {"readme.txt": "hi"})).kind == packages.UNKNOWN
