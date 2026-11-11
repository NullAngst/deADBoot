# deADBoot maintenance

A check to run every couple of months. deADBoot pins nothing, so the risk is drift: a URL moves, a project changes its zip layout, a runner image retires. Most of it is click-and-look. Budget an hour, plus the Pixel 9 Pro section if you're doing a release.

## Before you start

- A Linux box with the repo checked out and Python 3 with PyQt6 and pytest.
- The Pixel 9 Pro (`caiman`), charged above 50%, USB debugging on.
- Optional: a Windows machine for the last section.

## 1. CI and builds

1. Open the Actions tab and run the Build workflow by hand (workflow_dispatch). All four jobs should be green: Linux, emulator, Windows, and release skipped.
2. If the Linux job fails at the runner itself, GitHub has probably retired `ubuntu-22.04`. Change `runs-on` in `.github/workflows/build.yml` to the oldest Ubuntu LTS runner still offered, since an AppImage only runs on systems with a glibc at least as new as the one it was built on.
3. If the emulator job fails while starting the emulator, check which API level `build/latest-api.sh` picked. A brand-new API sometimes isn't ready in the emulator runner for a few weeks. That's the price of not pinning. Wait, or temporarily hardcode the previous level in the workflow and leave yourself a note in changes.md to remove it.
4. If the Windows job fails at the smoke test, read the printed self-test log. "module not found" or a missing data file means PyInstaller left something out; add it with `--hidden-import` or `--add-data` in `build/windows.ps1`.
5. Run the unit tests locally: `QT_QPA_PLATFORM=offscreen python -m pytest tests --ignore=tests/emulator`.

## 2. Download URLs and formats

Check these still resolve and return what `deadboot/downloads.py` expects:

1. Platform-tools zips: `https://dl.google.com/android/repository/platform-tools-latest-linux.zip` and `...-windows.zip`. The zip should still hold a single `platform-tools/` folder with `adb` and `fastboot`.
2. Google's SDK repository XML (`repository2-3.xml`, used by Check for tool updates) still lists a `platform-tools` package with a revision.
3. F-Droid index: `https://f-droid.org/repo/index-v2.json` still has `packages.com.looker.droidify.versions.*.file.sha256`. If F-Droid moves to a new index version, update `fetch_fdroid_apk`.
4. GitHub releases (`data/sources.toml`): Shizuku (RikkaApps/Shizuku), Obtainium (ImranR98/Obtainium), Install With Options (zacharee/InstallWithOptions), Magisk (topjohnwu/Magisk). Check each latest release still has an APK matching its `asset_regex`, and whether any project started publishing a checksum file (deADBoot picks it up automatically if it's named `*.sha256`, `SHA256SUMS` or `checksums.txt`).
5. Windows scrcpy: the latest Genymobile/scrcpy release still has a `scrcpy-win64-*.zip`.
6. python-appimage still publishes `python3.N` releases with `manylinux_2_28_x86_64` (or `manylinux2014`) AppImages, and appimagetool's `continuous` release still has `appimagetool-x86_64.AppImage`.

## 3. ROM profiles and pages

1. For each file in `data/roms/`, open every URL with `<codename>` replaced by `caiman` and make sure it lands on the right device section. Same for Google's factory image and OTA pages (`https://developers.google.com/android/images#caiman`, `.../ota#caiman`).
2. Read each project's current install guide against its profile:
   - GrapheneOS (`https://grapheneos.org/install/cli`): minimum fastboot version, signing method, the `allowed_signers` URL, the expected "Good" line, relock advice.
   - LineageOS wiki: install flow (recovery, sideload, the reboot before add-ons), whether caiman is official, extra images it lists.
   - Google: factory image instructions and `flash-all` usage.
3. Re-test package detection on a current release of each profiled ROM: drop the zip on the Flashing tab and confirm the type and target device are right. Projects change zip layouts quietly. If one changed, add a fixture zip that copies the new layout to `tests/conftest.py` and fix `deadboot/packages.py`.
4. Re-check the shipped recipes in `data/recipes/` against the current LineageOS, GrapheneOS and MindTheGapps steps. If Lineage changes its recovery flow, the recipe changes with it.

## 4. Google developer verification

1. Read `https://support.google.com/android-developer-console/answer/16561738` and Google's developer verification guide.
2. Has it reached the US, or gone global? Is ADB still a supported path for unverified apps? Has Google documented how it detects ADB installs, and does that cover Shizuku-based installers (which may not set the internal from-ADB flag `pm install` uses)?
3. Update the "honest limits" section of README.md if anything changed. If the Shizuku path stops working for unverified apps, say so plainly and point people at the cable install.

## 5. Device-side behavior

1. Shizuku: on the emulator or the Pixel, run Start Shizuku and confirm it still finds the starter (`libshizuku.so` in the package's lib folder, or `start.sh`). The emulator job covers this too.
2. Debloat do-not-touch list (`data/debloat-protected.txt`): skim release notes for current Pixel, Samsung and Android versions and add anything new that bootloops or locks you out when removed.
3. Install failures: compare `INSTALL_FAILED_*` codes in the current AOSP `PackageManager.java` with `INSTALL_FAILURES` in `deadboot/parsers.py`. Add plain sentences for new ones worth decoding.
4. udev: check your distro's rules package name hasn't changed (`android-sdk-platform-tools-common` on Kubuntu) and update `RULES_PACKAGES` in `deadboot/usbhelp.py`. Add any new phone maker's USB vendor ID to `VENDORS`.
5. Re-test install, push/pull and scrcpy against the Pixel or the emulator.

## 6. Release check on the Pixel 9 Pro

Only run a real unlock or full flash if you're planning one anyway. Otherwise tick "Show me the commands" and read the dry-run output.

1. Read state in the bootloader: unlock status, unlock ability, current slot, `has-slot:init_boot` should be `yes`.
2. Boot an image without flashing (`fastboot boot`), using the stock `boot.img` from the matching factory image. It should come up normally.
3. Magisk round trip on `init_boot`: read build, pick partition (expect `init_boot`), extract from the factory zip for the running build ID, push, patch in Magisk, pull, check passes. Flash only if you want root; otherwise stop after the check.
4. Restore stock: dry-run it and confirm it targets `init_boot_<active slot>` with the saved image.
5. Dry-run the GrapheneOS recipe and the factory-image flow and read every command.

## 7. Windows

1. Confirm the Windows platform-tools and scrcpy downloads, the Google USB Driver page (`https://developer.android.com/studio/run/win-usb`), Google's OEM driver list and Zadig (`https://zadig.akeo.ie/`) still load.
2. If a Windows machine is handy: download the release EXE, check its SHA-256, run it, connect the Pixel, and do one push, one pull and one APK install.

## 8. Wrap up

1. Bump `__version__` in `deadboot/__init__.py` to the date.
2. Add an entry to changes.md.
3. Tag `vYYYY.MM.DD` to publish. The release only goes out when the Linux, emulator and Windows jobs all pass.
