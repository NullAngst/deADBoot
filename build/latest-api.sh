#!/usr/bin/env bash
# Prints the newest numbered Android API level that has a google_apis x86_64 system image,
# so the emulator job tests against the current Android without a pinned version.
# Writes api=NN to $GITHUB_OUTPUT when that is set.
set -euo pipefail

sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[ -n "$sdk" ] || { printf '%s\n' "ANDROID_HOME is not set" >&2; exit 1; }
sdkmanager=""
for c in "$sdk/cmdline-tools/latest/bin/sdkmanager" "$(command -v sdkmanager || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then
        sdkmanager=$c
        break
    fi
done
[ -n "$sdkmanager" ] || { printf '%s\n' "sdkmanager not found" >&2; exit 1; }

best=0
while IFS= read -r line; do
    case "$line" in
        *"system-images;android-"*";google_apis;x86_64"*)
            level=${line#*system-images;android-}
            level=${level%%;*}
            case "$level" in
                ''|*[!0-9]*) continue ;;
            esac
            [ "$level" -gt "$best" ] && best=$level
            ;;
    esac
done < <(LC_ALL=C "$sdkmanager" --list 2>/dev/null)

[ "$best" -gt 0 ] || { printf '%s\n' "no google_apis x86_64 image listed" >&2; exit 1; }
printf 'Newest google_apis x86_64 image: API %s\n' "$best"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf 'api=%s\n' "$best" >>"$GITHUB_OUTPUT"
fi
