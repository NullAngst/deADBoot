import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    yield


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeTools:
    """Pretends every tool is on PATH at /usr/bin/<name>."""

    def path(self, name):
        return f"/usr/bin/{name}"

    def argv(self, name):
        return [f"/usr/bin/{name}"]

    def available(self, name):
        return True


@pytest.fixture
def runner(tmp_path):
    from deadboot.actionlog import ActionLog
    from deadboot.runner import Runner
    return Runner(FakeTools(), ActionLog(tmp_path / "logs"))


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def make_zip(tmp_path):
    def make(name: str, files: dict) -> Path:
        p = tmp_path / name
        p.write_bytes(_zip_bytes(files))
        return p
    return make


@pytest.fixture
def factory_zip(make_zip):
    inner = _zip_bytes({
        "android-info.txt": "require board=caiman\nrequire version-bootloader=ripcurrentpro-15.1-12451543\n"
                            "require version-baseband=g5400c-241205-241206-B-12701856\n",
        "boot.img": b"ANDROID!" + b"\0" * 56,
        "init_boot.img": b"ANDROID!" + b"\1" * 56,
        "vendor_boot.img": b"VNDRBOOT",
    })
    return make_zip("caiman-ap4a.250105.002-factory-1234.zip", {
        "caiman-ap4a.250105.002/flash-all.sh": "#!/bin/sh\nfastboot flash bootloader bootloader.img\n"
                                                "fastboot -w update image-caiman-ap4a.250105.002.zip\n",
        "caiman-ap4a.250105.002/flash-all.bat": "fastboot -w update image-caiman-ap4a.250105.002.zip\r\n",
        "caiman-ap4a.250105.002/bootloader-caiman.img": b"BL",
        "caiman-ap4a.250105.002/image-caiman-ap4a.250105.002.zip": inner,
    })


@pytest.fixture
def recovery_zip(make_zip):
    return make_zip("crdroid-15-caiman.zip", {
        "META-INF/com/google/android/update-binary": "#!/sbin/sh\n",
        "META-INF/com/google/android/updater-script":
            'assert(getprop("ro.product.device") == "caiman" || getprop("ro.build.product") == "caiman");\n',
        "system.new.dat.br": b"x",
    })


@pytest.fixture
def payload_zip(make_zip):
    return make_zip("lineage-22.1-20250110-nightly-komodo-signed.zip", {
        "payload.bin": b"CrAU",
        "payload_properties.txt": "FILE_HASH=abc\n",
        "META-INF/com/android/metadata": "ota-type=AB\npre-device=komodo\n"
                                          "post-build=google/komodo/komodo:15/AP4A/eng.x:user/release-keys\n"
                                          "post-timestamp=1736500000\n",
        "META-INF/com/google/android/update-binary": "#!/sbin/sh\n",
    })


@pytest.fixture
def nometa_zip(make_zip):
    return make_zip("vbmeta-images.zip", {"vbmeta.img": b"AVB0", "dtbo.img": b"DTBO"})


@pytest.fixture
def fastboot_update_zip(make_zip):
    return make_zip("image-update.zip", {
        "android-info.txt": "require product=caiman|komodo\n", "boot.img": b"ANDROID!", "system.img": b"s"})
