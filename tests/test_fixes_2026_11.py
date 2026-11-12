"""Regression tests for the window width, Obtainium detection, common bloat and flashing fixes."""

import os
import zipfile

import pytest

from deadboot import debloat, downloads, hostplatform, ops
from deadboot.ops import DeviceOps

# ---------------------------------------------------------------- Obtainium

OBTAINIUM_ASSETS = [{"name": n} for n in (
    "app-arm64-v8a-fdroid-release.apk", "app-arm64-v8a-release.apk",
    "app-armeabi-v7a-fdroid-release.apk", "app-armeabi-v7a-release.apk",
    "app-fdroid-release.apk", "app-release.apk",
    "app-x86_64-fdroid-release.apk", "app-x86_64-release.apk")]


def test_obtainium_never_picks_fdroid_flavor():
    src = downloads.load_sources()["obtainium"]
    for abi, want in (("arm64-v8a", "app-arm64-v8a-release.apk"), ("armeabi-v7a", "app-armeabi-v7a-release.apk"),
                      ("x86_64", "app-x86_64-release.apk"), (None, "app-release.apk")):
        a = downloads.pick_asset(OBTAINIUM_ASSETS, src["asset_regex"], abi, src["abi_assets"], src["asset_exclude"])
        assert a["name"] == want


def test_obtainium_counts_both_builds_as_installed():
    names = downloads.package_names(downloads.load_sources()["obtainium"])
    assert names == ["dev.imranr.obtainium", "dev.imranr.obtainium.fdroid"]
    assert downloads.package_names(downloads.load_sources()["droidify"]) == ["com.looker.droidify"]


# ---------------------------------------------------------------- common bloat

def test_common_bloat_parse_and_match():
    cb = debloat.parse_common("## Meta\ncom.facebook.*\ncom.instagram.android  # trailing note\n"
                              "## Carrier\ncom.att.callprotect\n")
    assert cb.group("com.facebook.katana") == "Meta"
    assert cb.group("com.facebook") == ""            # the prefix keeps its dot
    assert cb.group("com.instagram.android") == "Meta"
    assert cb.group("com.att.callprotect") == "Carrier"
    assert cb.group("com.att.callprotect.extra") == ""
    assert len(cb) == 3


def test_shipped_common_list_never_touches_protected():
    common = debloat.load_common()
    assert len(common) > 40
    protected = debloat.load_protected()
    overlap = sorted(p for p in protected if common.group(p))
    assert overlap == [], f"common bloat list matches do-not-touch packages: {overlap}"
    for must in ("com.facebook.katana", "com.facebook.appmanager", "com.amazon.mShop.android.shopping",
                 "com.zhiliaoapp.musically", "com.netflix.partner.activation", "com.dti.att"):
        assert common.group(must), must
    for keep in ("com.google.android.gms", "com.android.vending", "com.android.phone",
                 "com.tmobile.vvm.application", "com.att.mobile.android.vvm"):
        assert not common.group(keep), keep


def test_user_common_list_merges(tmp_path):
    user = hostplatform.data_dir() / "debloat-common.txt"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("## Mine\ncom.example.junk\n")
    assert debloat.load_common().group("com.example.junk") == "Mine"


# ---------------------------------------------------------------- flashing

def test_disable_verity_only_for_vbmeta(runner):
    o = DeviceOps(runner, "SER", planning=True, mode_hint="bootloader")
    o.flash("vbmeta", "vbmeta.img", disable_verity=True)
    o.flash("boot", "boot.img", disable_verity=True)
    cmds = [i.text for i in o.plan if i.kind == "cmd"]
    assert "--disable-verity" in cmds[0]
    assert "--disable-verity" not in cmds[1]


def test_zip_slip_sibling_prefix_is_refused(tmp_path):
    # "../dest-evil/x" resolves outside dest but shares its name as a string prefix.
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../dest-evil/x", b"no")
    with pytest.raises(ops.OpError):
        ops.extract_zip_to(z, tmp_path / "dest")
    assert not (tmp_path / "dest-evil").exists()


def test_zip_extract_normal(tmp_path, factory_zip):
    out = ops.extract_zip_to(factory_zip, tmp_path / "x")
    assert ops.find_script_dir(out) is not None


# ---------------------------------------------------------------- window width

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(qapp):
    from deadboot.actionlog import ActionLog
    from deadboot.runner import Runner
    from deadboot.settings import Settings
    from deadboot.tools import Tools
    from deadboot.ui.common import App
    from deadboot.ui.jobs import Jobs
    from deadboot.ui.mainwindow import MainWindow
    from deadboot import profiles
    settings = Settings()
    settings.flashing_acknowledged = True
    tools = Tools(settings)
    profs, _ = profiles.load_all()
    ctx = App(settings, tools, Runner(tools, ActionLog()), Jobs(), profs, debloat.load_protected())
    return MainWindow(ctx)


SAMPLE = "Removes carrier and vendor bloat without root, both methods are reversible. "


def text_px_per_char(font) -> float:
    """How wide one character of ordinary English text is in this font, measured on real text.

    QFontMetrics.averageCharWidth() reads a number stored in the font file, and fonts disagree about it:
    two runners both reporting Sans Serif 9pt said 8 px and 6 px. Measuring a real sentence doesn't depend
    on that.
    """
    from PyQt6.QtGui import QFontMetricsF
    return QFontMetricsF(font).horizontalAdvance(SAMPLE * 4) / (len(SAMPLE) * 4)


def width_report(qapp):
    win = _window(qapp)
    per = text_px_per_char(qapp.font())
    widths = {t.title: t.minimumSizeHint().width() / per for t in win.tab_list}
    window = win.minimumSizeHint().width() / per
    f = qapp.font()
    report = (f"font {f.family()} {f.pointSizeF()}pt, {per:.1f}px per character of text; "
              f"window {win.minimumSizeHint().width()}px = {window:.0f} chars; " +
              ", ".join(f"{k} {v:.0f} chars" for k, v in widths.items()))
    win.close()
    return window, widths, report


def test_window_fits_one_ordinary_monitor(qapp):
    """Measured in characters of text, not pixels, since each runner's offscreen platform uses its own font.

    Checked across seven fonts from 6.3pt to 14.4pt: the fixed build measures 100 to 176 characters per tab and
    123 to 255 for the window (fixed-pixel parts like the device panel count for more at small sizes). With the
    bug put back it measures 367+ per tab and 395+ for the window. The limits sit between the two.
    """
    window, widths, report = width_report(qapp)
    assert window <= 300, "main window too wide, a long unwrapped label or button row crept back in: " + report
    wide = [k for k, v in widths.items() if v > 250]
    assert not wide, f"too wide: {', '.join(wide)}. " + report


def test_debloat_ticks_survive_filtering(qapp, monkeypatch):
    from deadboot.ui.tabs import apps
    shown = []
    monkeypatch.setattr(apps, "info", lambda _parent, _title, text: shown.append(text))
    win = _window(qapp)
    try:
        page = win.tabs_by_name["Apps"].debloat
        page.data = [("com.facebook.appmanager", "enabled", "system"), ("com.android.phone", "enabled", "system"),
                     ("com.samsung.android.app.spage", "enabled", "system"), ("com.example.ok", "enabled", "system")]
        page.guard = {"com.android.phone"}
        page.vendor.setCurrentText("Google")
        page.mark_common()
        assert page.vendor.currentText() == "All"          # marking shows everything it ticked
        assert "Nothing has been removed" in shown[-1]
        page.vendor.setCurrentText("Samsung")      # hides the Facebook row
        page.search.setText("spage")
        page.vendor.setCurrentText("All")
        page.search.clear()
        assert set(page.checked()) == {"com.facebook.appmanager", "com.samsung.android.app.spage"}
        # Ticked rows sort to the top, protected rows can't be ticked.
        top = [page.table.item(i, 0).text() for i in range(2)]
        assert set(top) == {"com.facebook.appmanager", "com.samsung.android.app.spage"}
        page.marked.add("com.android.phone")
        assert "com.android.phone" not in page.checked()
    finally:
        win.close()
