#!/usr/bin/env bash
# Runs inside the emulator job once the emulator has booted.
set -euo pipefail

for c in adb python3; do
    command -v "$c" >/dev/null 2>&1 || { printf 'missing command: %s\n' "$c" >&2; exit 1; }
done

apks=$(mktemp -d "${TMPDIR:-/tmp}/deadboot-test-apks.XXXXXX")
trap 'rm -rf -- "$apks"' EXIT
bash tests/emulator/build_test_apks.sh "$apks"

adb wait-for-device
# Wait for the package manager, not just adbd.
i=0
while [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]; do
    i=$((i + 1))
    [ "$i" -lt 120 ] || { printf '%s\n' "emulator never finished booting" >&2; exit 1; }
    sleep 2
done
adb shell input keyevent 82 >/dev/null 2>&1 || true

# boot_completed comes early on a fresh image: storage and adbd can still restart for a while.
# Wait until shared storage is usable and adb has answered ten checks in a row.
steady=0
tries=0
while [ "$steady" -lt 10 ]; do
    tries=$((tries + 1))
    [ "$tries" -lt 150 ] || { printf '%s\n' "emulator never became steady" >&2; exit 1; }
    if [ "$(adb shell 'ls -d /sdcard/Download >/dev/null 2>&1 && pm path android >/dev/null 2>&1 && echo ok' 2>/dev/null | tr -d '\r')" = "ok" ]; then
        steady=$((steady + 1))
    else
        steady=0
    fi
    sleep 1
done
printf 'emulator steady after %s checks\n' "$tries"

ANDROID_SERIAL=$(adb devices | awk 'NR > 1 && $2 == "device" { print $1; exit }')
export ANDROID_SERIAL
export DEADBOOT_EMULATOR=1
export DEADBOOT_TEST_APKS="$apks"
export QT_QPA_PLATFORM=offscreen
if ! python3 -m pytest -v -rA tests/emulator; then
    # Say why, so a failure here is diagnosable from the Actions log alone.
    printf '\n%s\n' "---- adb devices ----" >&2
    adb devices -l >&2 || true
    printf '%s\n' "---- crash log ----" >&2
    adb logcat -b crash -d -t 200 >&2 || true
    printf '%s\n' "---- adbd and storage lines ----" >&2
    adb logcat -d -t 4000 2>/dev/null | grep -i -E 'adbd|vold|MediaProvider.*(crash|died)|lowmemorykiller' | tail -n 80 >&2 || true
    exit 1
fi
