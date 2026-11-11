#!/usr/bin/env bash
# Builds the tiny APKs the emulator tests install: a base app, a feature split for it,
# and an app targeting SDK 17 for the low-target-SDK block. No code, manifest only.
# Uses the newest build-tools and platform already on the machine (GitHub's runners ship them).
#
# Usage: bash tests/emulator/build_test_apks.sh OUTPUT_DIR
set -euo pipefail

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

[ "$#" -eq 1 ] || die "usage: build_test_apks.sh OUTPUT_DIR"
out=$1
sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[ -n "$sdk" ] || die "ANDROID_HOME is not set"

missing=""
for c in keytool zip sort; do
    command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
done
[ -z "$missing" ] || die "missing commands:$missing"

# Highest version directory under a parent. Globs, not ls; sort -V is GNU-only but present on the CI runner.
newest() {
    local parent=$1 d best=""
    for d in "$parent"/*/; do
        [ -d "$d" ] || continue
        best=$(printf '%s\n%s\n' "$best" "${d%/}" | LC_ALL=C sort -V | tail -n 1)
    done
    printf '%s\n' "$best"
}

bt=$(newest "$sdk/build-tools")
[ -n "$bt" ] || die "no build-tools under $sdk"
plat=""
for d in "$sdk"/platforms/android-*/; do
    [ -f "${d}android.jar" ] || continue
    plat=$(printf '%s\n%s\n' "$plat" "${d%/}" | LC_ALL=C sort -V | tail -n 1)
done
[ -n "$plat" ] || die "no platform android.jar under $sdk"

aapt2="$bt/aapt2"
zipalign="$bt/zipalign"
apksigner="$bt/apksigner"
for t in "$aapt2" "$zipalign" "$apksigner"; do
    [ -x "$t" ] || die "not found: $t"
done

mkdir -p -- "$out"
work=$(mktemp -d "${TMPDIR:-/tmp}/deadboot-apks.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

ks="$work/test.keystore"
keytool -genkeypair -keystore "$ks" -storepass deadboot -keypass deadboot -alias test -keyalg RSA \
    -keysize 2048 -validity 3650 -dname "CN=deADBoot test" >/dev/null 2>&1

build() {
    local name=$1 manifest=$2
    printf '%s\n' "$manifest" >"$work/$name.xml"
    "$aapt2" link -I "$plat/android.jar" --manifest "$work/$name.xml" -o "$work/$name.unaligned.apk"
    "$zipalign" -f 4 "$work/$name.unaligned.apk" "$work/$name.aligned.apk"
    "$apksigner" sign --ks "$ks" --ks-pass pass:deadboot --key-pass pass:deadboot \
        --out "$out/$name.apk" "$work/$name.aligned.apk"
}

build base '<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="dev.deadboot.testapp" android:versionCode="1" android:versionName="1">
  <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34"/>
  <application android:hasCode="false" android:label="deADBoot test"/>
</manifest>'

build split '<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="dev.deadboot.testapp" split="extra" android:isFeatureSplit="true"
    android:versionCode="1" android:versionName="1">
  <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34"/>
  <application android:hasCode="false"/>
</manifest>'

build oldsdk '<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="dev.deadboot.oldapp" android:versionCode="1" android:versionName="1">
  <uses-sdk android:minSdkVersion="9" android:targetSdkVersion="17"/>
  <application android:hasCode="false" android:label="deADBoot old"/>
</manifest>'

printf 'built test APKs in %s with %s and %s\n' "$out" "${bt##*/}" "${plat##*/}"
