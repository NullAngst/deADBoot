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

ANDROID_SERIAL=$(adb devices | awk 'NR > 1 && $2 == "device" { print $1; exit }')
export ANDROID_SERIAL
export DEADBOOT_EMULATOR=1
export DEADBOOT_TEST_APKS="$apks"
export QT_QPA_PLATFORM=offscreen
python3 -m pytest -v tests/emulator
