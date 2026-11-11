# Changes

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
