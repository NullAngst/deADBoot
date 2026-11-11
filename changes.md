# Changes

2026.11.11 - Emulator test hardening

* tests/emulator/run.sh waits until shared storage is usable and adb has answered ten checks in a row before starting, and prints adb devices, the crash log and adbd/storage log lines when a test fails
* Every emulator test now waits for the device to be online first, so one dropped connection can't fail the test after it
* test_push_pull retries once and reports which case it hit: the emulator dropping out (passes, with a note), pull -a failing repeatedly while plain pull works (fails), or pull failing outright (fails)
* More candidate system apps for the remove-and-restore test, which was skipped on the newest image

Reasons for changes: the first emulator run in Actions lost its adb connection for a moment. A pull exited 1 with no output, the next test got "device offline", and the three tests after that passed. The log could not say whether the new image was still settling or whether pull -a caused it, so the test now tells those apart.

2026.11.11 - Build fix

* build/appimage.sh makes the AppDir world-readable (chmod -R a+rX) before packing
* The AppImage icon is now a 256x256 PNG rendered from data/deadboot.svg with the bundled Qt; the SVG .DirIcon is gone
* Desktop entry lists one main category (Utility)

Reasons for changes: the first AppImage build in Actions failed. mktemp creates the build folder with mode 0700 and the current appimagetool refuses to pack an AppDir other users can't read. It also warned that an SVG .DirIcon will become an error in a future release.

2026.11.11 - Initial release

* PyQt6 desktop app wrapping adb, fastboot, heimdall and scrcpy, with one runner that every command goes through: argument lists only, serial always added, live log pane, daily action log kept 30 days, dry-run mode
* Device panel with live adb and fastboot detection, USB hotplug on Linux, per-device details, guidance for unauthorized, no-permissions and offline, wireless debugging pairing, mDNS discovery and tcpip 5555
* Files tab: remote browser, push/pull queue with per-file retry and skip-if-already-there, rescue pulls, timestamp preservation, Windows filename rules with renamed-files.txt
* Apps tab: installed apps, install of single, split and .apks/.xapk bundles with decoded INSTALL_FAILED_* errors and offered fixes (low-target-SDK bypass, downgrade, uninstall-then-install), quick installs of Shizuku, Droid-ify, Obtainium, Install With Options and Magisk with hash checks
* Debloat: disable or remove for user 0, do-not-touch list plus the live launcher and keyboard, per-device record with restore, re-apply, export and import
* F-Droid Setup tab: Shizuku via Play or GitHub, client install, Shizuku start using its own starter, root mode when root is granted
* Backup tab: adb backup builder, .ab extraction (built in, abe for encrypted), rescue sets
* Flashing & Root tab behind an acknowledgement: package detection by contents, device match from package metadata with hard block, bootloader/radio and rollback warnings, SHA-256 plus ssh-keygen, gpg and minisign verification, factory image, fastboot update, recovery sideload with ordered add-ons and loose-image flows, fastbootd split, slots, unlock with code, relock rule, Magisk patched-image round trip with restore stock, heimdall for Samsung
* ROM profiles for Stock, GrapheneOS, LineageOS and Generic as plain TOML
* Recipes: TOML step lists with inputs, prechecks, full plan view, one typed confirmation, stop and resume; editor, duplicate, shipped examples
* Diagnostics tab: filtered live logcat, getprop, getvar all, battery and storage, bug report
* Settings, tool resolution (PATH, settings, fetched platform-tools), version check against Google's repository, udev and Windows driver guidance shown as commands only
* Five themes (normal light, normal dark, verdigris, Kawaii Light, Kawaii Dark) with danger styling in each
* GitHub Actions: unit tests, AppImage on the oldest Ubuntu LTS runner, emulator tests on the newest system image, Windows one-file EXE with a self-test smoke check, release only when all pass

Reasons for changes: first complete build from the deADBoot build spec. Developer verification wording in the README follows Google's current page (Indonesia, Singapore and Thailand from 2026-09-30, global in 2027) instead of the spec's earlier list.
