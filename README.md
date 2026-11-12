# deADBoot

deADBoot is a desktop app for Linux and Windows that drives `adb`, `fastboot`, `heimdall` and `scrcpy` for you, so you can rescue data off old or dying Android phones, move files without MTP, install and keep F-Droid apps updated the privileged way, mirror a phone with a dead screen, and unlock, flash and root devices that allow it.

It's for people who own the phone in front of them and are tired of typing half-remembered commands. Every command deADBoot runs is shown in the log pane, so you always know what it did.

## Read this first: the honest limits

- **Developer verification.** Google is requiring sideloaded apps to come from registered developers. Per Google's own page, it goes live on September 30, 2026 in Indonesia, Singapore and Thailand, starting with installs from participating app stores, and expands to all apps on certified devices globally in 2027. Google says developers and power users can still install unverified apps over ADB. That's why deADBoot's install path is `adb install`. Installs through Shizuku run with the same shell privileges, so they're expected to get the same treatment, but Google hasn't said how it tells an ADB install apart. **Google has not promised the ADB route forever.** If it changes, the install features here are the ones affected. AOSP builds and non-certified devices (LineageOS without Google apps, for example) are outside the scheme entirely.
- **OEM unlocking is on the phone.** The toggle in Developer options can't be flipped over adb. On some carrier phones it's greyed out for good, and then the bootloader will never unlock. deADBoot reads the unlock ability and tells you before you start.
- **Unlocking needs your hands.** The phone asks you to confirm with its volume and power buttons. By design, nothing can automate that.
- **Unlocking wipes the phone.** Pull your data first. deADBoot asks, and offers the rescue pull right there.
- **Device match.** deADBoot reads the target device out of the package's own metadata and blocks the flash outright on a mismatch. A match proves the ROM was built for your device, not that the build is any good.
- **NEVER RELOCK THE BOOTLOADER UNLESS THE INSTALLED ROM SUPPORTS IT.** Relocking on a ROM that isn't signed for your device's verified boot, or on a rooted image, hard-bricks many devices. deADBoot only offers relock when it did the last flash itself, that flash was stock or a ROM whose profile supports relocking (GrapheneOS does), and no Magisk image went on since.
- **Samsung is the hard case.** Heimdall is barely maintained and fails on newer Samsung bootloaders more often than it works. US Snapdragon models generally can't be unlocked at all. Unlocking trips Knox permanently: Samsung Pay, Secure Folder and some banking apps stop working for good, even after relocking.
- **Dead screen, never authorized.** adb needs you to tap Allow on the phone once. If the screen is dead and you never did, scrcpy can't help, because scrcpy needs adb too. Help, Dead screen lists the workarounds that sometimes work.
- **Out of scope.** EDL, MediaTek BROM and other vendor rescue modes. Xiaomi's Mi Unlock waiting period. Carrier-locked bootloaders.

## Prerequisites

### Linux

- An x86_64 distro new enough to run the AppImage (glibc 2.35 or newer, so Ubuntu 22.04, Debian 12, Fedora 36 and later, or whichever).
- `adb` and `fastboot`. If they're missing, deADBoot offers to fetch Google's current platform-tools into `~/.local/share/deadboot/platform-tools`. Distro packages often lag years behind, so the fetched copy is usually the better one.
- USB permissions (udev rules plus group membership). This is the number one reason `adb devices` comes back empty or says "no permissions". See Install, step 3.
- Optional: `scrcpy` for mirroring, `heimdall` for Samsung, `payload-dumper-go` for rooting LineageOS-style ROMs, `ssh-keygen` (OpenSSH 8.1+) for GrapheneOS signatures, `gpg` or `minisign` for other signed ROMs, `lz4` for Samsung firmware, `abe` for encrypted backups. Features grey out with a tooltip naming the package when one's missing.

### Windows

- Windows 10 or 11, 64-bit.
- The USB driver for your phone. Pixels need the Google USB Driver for fastboot. Other brands need their own. deADBoot spots the symptom (Windows sees the phone, adb doesn't) and points you at the right download.
- For Samsung with heimdall: Zadig to bind the WinUSB driver in download mode. USB help walks you through it.
- `ssh-keygen` for GrapheneOS signatures ships with Windows' OpenSSH Client feature, usually already installed.

## Install

### Linux

1. Download `deADBoot-x86_64.AppImage` and its `.sha256` from the latest release.
2. Check it: `sha256sum -c deADBoot-x86_64.AppImage.sha256`
3. Set up USB access. I'm on Kubuntu, so in my case that's:
   ```
   sudo apt install android-sdk-platform-tools-common
   sudo usermod -aG plugdev "$USER"
   ```
   It depends on your system: Arch uses `android-udev` and the `adbusers` group, Fedora's `android-tools` brings the rules, and so on. USB help in deADBoot detects your package manager and group and shows the exact commands for your machine, plus the full rules file if your distro has no package. deADBoot never asks for root itself.
4. Log out and back in if you were added to a group, since group changes only apply to new sessions.
5. Make it executable: `chmod +x deADBoot-x86_64.AppImage`
6. Run it: `./deADBoot-x86_64.AppImage`
7. If adb and fastboot aren't found, accept the offer to fetch platform-tools.

### Windows

1. Download `deADBoot.exe` and `deADBoot.exe.sha256` from the latest release.
2. Check the hash in PowerShell: `Get-FileHash -Algorithm SHA256 .\deADBoot.exe` and compare it with the `.sha256` file.
3. Run it. The build isn't code-signed (a certificate is a paid yearly cost), so SmartScreen says "Windows protected your PC". Click More info, then Run anyway, but only if the hash matched. Some antivirus tools flag PyInstaller builds in general.
4. Accept the offer to fetch platform-tools, and scrcpy if you want mirroring.

It's one file, no installer. It unpacks itself to a temp folder each launch, so it starts a few seconds slower than a normal program.

### On the phone

1. Settings, About phone, tap Build number seven times.
2. Settings, System, Developer options, turn on USB debugging.
3. Plug it in and tap Allow on the prompt (tick Always allow from this computer).

## Usage by tab

`Ctrl+1` to `Ctrl+7` switch tabs. Alt plus the underlined letter presses a button. Destructive actions ask you to type the device's model name, and every confirmation names the device and serial so two plugged-in phones are never ambiguous.

The **device panel** on the left lists everything adb and fastboot see, with mode, battery, storage, lock state, slot and root. Unauthorized, no-permissions and offline get a plain explanation and a fix button. **Wireless debugging** in the top bar pairs (Android 11+), discovers phones over mDNS, or switches older phones to `adb tcpip 5555` over the cable first. **Mirror screen** runs scrcpy with resolution, bitrate, recording, stay-awake, screen-off and audio options.

**1 Files.** Browse the phone, pull or push with a queue, drop files on the window to push them. Rescue pulls grab all of `/sdcard` or just DCIM and Pictures. `adb pull` can't resume a half-copied file, so the queue works per file: finished files stay finished, the interrupted one restarts, and anything already on disk at the same size is skipped on retry. Preserve timestamps keeps photo dates. On Windows, names Windows refuses are rewritten and listed in `renamed-files.txt`.

**2 Apps.** Installed apps (filter, uninstall with or without data, enable, disable, pull every split APK). Install takes APKs, split sets and `.apks`/`.xapk` bundles, with reinstall, downgrade, grant-all and the Android 14+ low-target-SDK bypass. Failures come back as a plain sentence, and deADBoot offers the fix when there is one (the bypass for old apps, downgrade, or uninstall-then-install on a signature mismatch). Quick installs fetch the current Shizuku, Droid-ify, Obtainium, Install With Options or Magisk. **Debloat** disables or removes bloat for user 0, both reversible without a factory reset. The do-not-touch list (`data/debloat-protected.txt`, plus your launcher and keyboard at runtime) can't be ticked. Press Load packages, then **Mark common bloat** to tick everything on the common bloat list (`data/debloat-common.txt`: Facebook, Amazon, TikTok, Netflix, carrier extras from Verizon, AT&T and T-Mobile, silent app installers like Digital Turbine and ironSource Aura, a few Samsung and Xiaomi extras). It only ticks. Ticked rows sort to the top with their group in the Note column, so you scroll through, untick what you use, tick anything else, then press Disable ticked or Remove ticked yourself. Ticks survive the vendor filter and search. Every change is recorded per device, so Restore, Re-apply after an OTA and Export to a list file all work.

**3 F-Droid Setup.** Sets up Shizuku and an app store client so the phone installs and updates apps by itself. The tab walks through it in order; the full steps are in [Setting up Shizuku and an F-Droid client](#setting-up-shizuku-and-an-f-droid-client) below.

**4 Backup.** The `adb backup` builder, `.ab` extraction (built in for unencrypted backups, `abe` for encrypted ones) and the rescue sets. `adb backup` is deprecated and backs up almost nothing on Android 12+, but it still works on the old phones it's useful for.

**5 Flashing & Root.** Hidden behind an "I understand this can brick a device" button the first time. Covered below.

**6 Recipes.** Saved step lists. Covered below.

**7 Diagnostics.** Live logcat with level, tag and package filters, pause and save. Searchable getprop. `fastboot getvar all`. Battery and storage. A bug report bundle.

## Setting up Shizuku and an F-Droid client

Shizuku is a helper app that lets other apps do what adb can, like installing without a confirmation prompt each time. Droid-ify (F-Droid apps) or Obtainium (apps from GitHub, GitLab and Codeberg releases) then uses it to install and update apps on the phone, no computer needed.

You do not need Wireless debugging or pairing to set this up. Shizuku's service has to be started by adb, and deADBoot does that over the USB cable. Wireless debugging is only for restarting Shizuku after a reboot when you're away from the computer.

Prerequisites: the phone connected with USB debugging allowed (see [On the phone](#on-the-phone)).

1. Open the F-Droid Setup tab and pick the client: Droid-ify or Obtainium.
2. Press 1. Get Shizuku. With the Play Store on the phone, Shizuku's Play listing opens there: tap Install, and deADBoot carries on by itself once it sees it installed. Without Play, deADBoot installs the latest GitHub release.
3. Press 2. Install the client. If the client is already on the phone, as either its GitHub build or its F-Droid build, it's left alone.
4. Press 3. Start Shizuku. Then press Open Shizuku on the phone: the top card should say Shizuku is running. Ignore Shizuku's own Start via Wireless debugging and Pairing buttons, since those are the cable-free way of doing this same step.
5. Press Open the client on the phone, then point it at Shizuku:
   - Droid-ify: Settings, Installer, choose Shizuku.
   - Obtainium: Settings, turn on "Use Shizuku or Sui to install".
6. The client asks to access Shizuku. Tap Allow. Apps only show up in Shizuku's authorized list after they ask, so an empty list before this step is normal. You never add apps from inside Shizuku.
7. Install apps. In Droid-ify, search and tap Install. In Obtainium, tap Add App, paste the app's project page (for example `https://github.com/owner/app`), tap Add, then Install. With Shizuku there's no "Do you want to install this app?" prompt, for installs or updates.

Check status shows what's installed (and which build), whether Shizuku is running, and whether Play is present.

Without root, Shizuku stops at every reboot. The client still browses, but installs fail until Shizuku runs again. Plug in and press 3. Start Shizuku, or set up the cable-free restart once (Android 11+):

1. Put the phone on a Wi-Fi network you trust.
2. Developer options, turn on Wireless debugging.
3. In Shizuku, tap Pairing under Start via Wireless debugging. Allow its notifications if asked.
4. In Developer options, Wireless debugging, tap Pair device with pairing code.
5. Type the 6-digit code into Shizuku's notification.
6. In Shizuku, tap Start.

After that, each reboot is: Wireless debugging on (Android turns it off when Wi-Fi drops), open Shizuku, tap Start. On a rooted phone, pick Root mode in the tab instead, and Shizuku starts itself at boot.

## Flashing and installing ROMs

deADBoot never downloads a ROM. You supply the files; it opens the project's pages, tells you what to grab, and checks what you picked.

1. Pick a ROM profile and press Install guide and Downloads. The checklist says exactly which files to get, all from the same build.
2. Drop the zip (and its `.sig` if there is one) on the tab. deADBoot identifies the package by what's inside it: factory image, fastboot update, recovery zip, OTA payload or loose images.
3. It compares the package's own target device with the phone and blocks a mismatch. It also checks bootloader and radio versions where the package lists them, and warns when the image looks older than what's installed (anti-rollback can brick Pixel 6 and newer).
4. Paste the published SHA-256, or let it verify the signature with `ssh-keygen`, `gpg` or `minisign`. Unverified packages are flagged in red in the confirmation.
5. Run the flow one click at a time. Tick "Show me the commands" first if you want a dry run: device-changing commands are printed instead of run.

Factory images run the package's own `flash-all.sh` (or `flash-all.bat` on Windows) with `ANDROID_SERIAL` set, so a second plugged-in phone can't get hit. Keep data edits a temporary copy of the script without `-w`, never the original. The battery check refuses to start below 30% (changeable in Settings) unless you type an override.

For GrapheneOS you also need `allowed_signers` from its install guide, once. deADBoot keeps a copy under its data folder and warns if a later one differs.

## Root with Magisk, and redoing it after an update

The boot image has to match the exact build on the phone, so the Root section is ordered steps:

1. Read build: fingerprint, build ID and active slot from the booted phone.
2. Pick partition: reboots to the bootloader, asks `has-slot:init_boot`, compares with the package, reboots back. If they disagree it stops.
3. Get stock image: straight off a rooted phone with `dd`, out of the factory zip whose build ID matches, or out of the ROM zip's `payload.bin` with `payload-dumper-go`.
4. Install Magisk if needed and push the image to `/sdcard/Download/`.
5. Patch it in the Magisk app (Install, Select and patch a file), then Pull patched image. deADBoot checks the header and size.
6. Test boot (only `boot` devices; `init_boot` devices like the Pixel 9 Pro have no test boot).
7. Flash it to the active slot.

Stock and patched images are kept per build in `~/.local/share/deadboot/devices/<serial>/<build-id>/` (Windows: `%LOCALAPPDATA%\deADBoot\devices\...`). **Restore stock image** flashes the stock one back from the bootloader, which is the fix for a bootloop.

Every OTA or ROM update replaces the boot image and drops root. To redo it, boot the updated phone and run steps 1 to 7 again with the new build's package. deADBoot keeps the last two builds and cleans up older ones.

## Recipes

A recipe is a TOML file in `~/.local/share/deadboot/recipes/` (Windows: `%LOCALAPPDATA%\deADBoot\recipes\`). The shipped examples (LineageOS + MindTheGapps, LineageOS for microG, GrapheneOS on a Pixel, New phone setup) are copied there on first run so you can edit them.

Running one asks for every input up front, checks all of them (device match, hashes, signatures, battery, bootloader state, fastboot version), shows the whole plan with every command, and asks for one typed confirmation. Any failed step stops the run; you can resume from it once fixed. The guardrails still apply inside a recipe.

Step actions: `reboot`, `flash`, `boot_image`, `sideload`, `flash_all`, `fastboot_update`, `install_apk`, `push`, `pull`, `debloat`, `shizuku_setup`, `magisk_root`, `set_active_slot`, `unlock`, `lock`, `wait`, `note`. The editor in the Recipes tab writes the same TOML you'd write by hand, and errors name the line.

Debloat lists live in `recipes/lists/`, one package per line, `#` for comments. A list runs without per-package questions, skips packages that aren't installed, and still refuses anything on the do-not-touch list.

## Writing a ROM profile

Add one `.toml` file to `~/.local/share/deadboot/roms/` (Windows: `%LOCALAPPDATA%\deADBoot\roms\`). No code change. A profile with the same `id` as a shipped one replaces it. Example:

```toml
id = "e-os"                       # unique, lowercase
name = "/e/OS"
description = "Shown in the Get the files panel"
package_types = ["recovery_zip"]  # factory_image, fastboot_update, recovery_zip, ota_payload, loose_images
install_guide_url = "https://doc.e.foundation/devices/<codename>/install"
downloads_url = "https://doc.e.foundation/devices/<codename>"
devices_url = "https://doc.e.foundation/devices"
xda_url = "https://xdaforums.com/search/?q=<codename>"   # optional, for community builds
verify = "sha256"                 # sha256, ssh-keygen, gpg, minisign or none
relock_supported = false          # only true if the ROM signs for verified boot
root_supported = true
min_fastboot = ""                 # e.g. "35.0.1" when the project requires it
pixel_only = false
checklist = ["The /e/OS zip for <codename>", "Its SHA-256", "The /e/OS recovery from the same build"]

[signature]                       # only for verify = "ssh-keygen" (others need nothing here)
namespace = "factory images"
identity = "contact@example.org"
key_url = "https://example.org/allowed_signers"
expect = 'Good "factory images" signature'

[[pre_steps]]
text = "Anything the guide says to do before flashing"

[[post_steps]]
text = "Anything after"
```

`<codename>` is replaced with the connected device's codename. A broken profile is reported in the log and skipped.

## Where things live

- Settings: `~/.config/deadboot/settings.toml` (Windows: `%APPDATA%\deADBoot\settings.toml`)
- Data, fetched tools, cached APKs, boot images, recipes: `~/.local/share/deadboot/` (Windows: `%LOCALAPPDATA%\deADBoot\`)
- Your own debloat lists: `debloat-protected.txt` (more do-not-touch packages) and `debloat-common.txt` (more packages for Mark common bloat) in the data folder. Same format as the shipped files in `data/`, merged with them.
- Action log: `logs/` in the data folder, one file per day, every device command with time and serial, kept 30 days.

No telemetry. The only network calls are downloads you trigger (platform-tools, scrcpy on Windows, APKs from F-Droid and GitHub) and the tool update check.

## Running from source

```
pip install PyQt6
python -m deadboot
```

Tests: `pip install pytest`, then `QT_QPA_PLATFORM=offscreen python -m pytest tests --ignore=tests/emulator`.

Now deADBoot is running, every command it sends is in front of you, and your phone does what you told it to. Tinker as you see fit: profiles, recipes and the do-not-touch list are all plain text.

## License

GPL-3.0. See `LICENSE`.
