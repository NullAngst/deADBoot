# Changes

2026.11.12 - Build fix

* The window-width test measures in characters of real text: it times a sample sentence in the font in use and divides by that. Limits: 300 characters for the window, 250 for any tab
* When it fails, it prints the font in use and every tab's width, so the log shows which tab grew

Reasons for changes: the new window-width test broke both builds. A pixel limit failed on Windows, where the offscreen platform draws text about 1.5x wider (1456 px there, 930 px on Linux). Switching to Qt's "average character width" then failed on Linux: that number is read from the font file, and the runner's font said 6 px where the same-named font here says 8 px, so a fine 952 px window counted as too wide. Measured on real text across seven fonts from 6.3pt to 14.4pt, the fixed build is at most 176 characters per tab and 255 for the window, and the original bug is at least 367 and 395. The limits sit between the two.

2026.11.12 - UX overhaul / Bug fixes

* Main window no longer opens across several monitors. Long help lines in the Files, Apps and Backup tabs were not word-wrapped, so the Apps tab alone demanded 2161 px; it now needs under 700 and the whole window under 950. The window opens at 1360x880 or 92% of the screen it starts on, whichever is smaller, centered. A safety net wraps any long label, and a new test fails if any tab grows past 820 px
* Recipes, Install and Debloat button rows split into two rows
* Obtainium: Check status said "not installed" right after installing it. Obtainium's GitHub releases ship F-Droid-flavor APKs (package dev.imranr.obtainium.fdroid) next to the normal ones, and deADBoot picked the F-Droid one. sources.toml gains asset_exclude (Obtainium skips anything named fdroid) and alt_packages (either build counts as installed, so a second copy is never installed next to the first)
* F-Droid Setup tab rebuilt as a guided page: what Shizuku is, that setup needs no Wireless debugging, steps 1 to 3 with a line on what each does, then what to press on the phone in Droid-ify and in Obtainium, how apps get into Shizuku's list (they ask; you never add them in Shizuku), how to install apps in each client, reboots, and the optional cable-free restart with the pairing steps in order
* New buttons: Open Shizuku on the phone, Open the client on the phone
* Get Shizuku with Play present now waits for the Play install to finish (up to five minutes) instead of stopping at "the listing is open". The recipe step does the same instead of asking you to click Continue
* Debloat: Mark common bloat ticks every loaded package on the new data/debloat-common.txt list and never removes anything. Ticked rows sort to the top with their group in a Note column, a counter shows how many are ticked, and Show ticked only filters to them. You add your own entries in debloat-common.txt in the data folder
* Debloat: user-installed packages now show when they are on the common bloat list or in the device's debloat record, since carrier setup installers drop TikTok and games in as user apps. New Type column
* Debloat: ticks were stored only in the table, so changing the vendor filter or the search box cleared them. They are kept separately now
* Debloat: Restore reports which packages could not come back instead of counting them silently as failures
* Flashing: the loose-image partition mapping was rebuilt on every device refresh (every reboot, every Read state), throwing away what you had mapped. It is now filled only when a package is loaded
* Flashing: flash-all, fastboot update and sideload buttons were visible before any package was chosen, and fastboot update and Sideload crashed with no package. They stay hidden until a matching package is loaded
* Flashing: fastboot update refuses a folder (it needs a zip) and points at the loose image table
* Flashing: --disable-verity and --disable-verification are only passed for vbmeta partitions
* Flashing: when a bootloader doesn't answer getvar is-logical, the usual logical partitions (system, vendor, product and friends) are treated as logical if the device has a super partition, so they get flashed from fastbootd
* Flashing: the multi-GB folder a factory image is unpacked into is deleted after flash-all, pass or fail
* Flashing: after sideloading a ROM on an A/B device, the result and the add-on confirmation say to reboot to recovery first so add-ons land on the new slot
* Flashing: heimdall asks for the partition name before opening the file picker
* Root: step 2 reads the partition list over adb when the phone allows it, which skips the reboot to the bootloader and back. Reading a partition as root also checks /dev/block/bootdevice/by-name for older devices
* Zip extraction (factory images, platform-tools, scrcpy) compared paths as strings, which let a "../dest-evil" entry through. It now checks real path containment
* Self-test checks debloat-common.txt is bundled

Reasons for changes: the window was unusable on a three-monitor desktop; the Shizuku guide left you guessing whether pairing was needed and how a client gets Shizuku access; Obtainium was installed under a package name the status check didn't know; you asked for a common-bloat preset that only marks; and the flashing review turned up the mapping wipe, the no-package crash and the smaller issues above.

2026.11.11 - CI hardening

* New build/apt-install.sh retries apt up to five times with growing waits; the Linux job uses it for the Qt runtime libraries
* The emulator job no longer installs any apt packages, since its tests only use QtCore
* build/apt-install.sh added to the shellcheck step

Reasons for changes: a release run failed in the Linux and emulator jobs because GitHub's runners could not resolve Ubuntu's package mirrors during apt-get. Nothing in deADBoot was at fault, but the emulator job did not need apt at all and the Linux job can ride out a short outage.

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
