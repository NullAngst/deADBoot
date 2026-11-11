#!/usr/bin/env bash
# Builds deADBoot-x86_64.AppImage: a portable Python from python-appimage, PyQt6 from pip,
# the deadboot package and data/, packed with appimagetool. Nothing is pinned: the newest
# python-appimage Python, the current PyQt6 and appimagetool's continuous build are used.
#
# Usage: bash build/appimage.sh   (from the repo root; output lands in dist/)
set -euo pipefail

die() {
    printf 'appimage.sh: %s\n' "$*" >&2
    exit 1
}

missing=""
for c in curl python3 sha256sum find; do
    command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
done
[ -z "$missing" ] || die "missing commands:$missing"

[ -f deadboot/__init__.py ] || die "run this from the repo root"
root=$(pwd)
version=$(python3 -c 'import re,sys; print(re.search(r"__version__ = \"([^\"]+)\"", open("deadboot/__init__.py").read()).group(1))')
arch=x86_64

work=$(mktemp -d "${TMPDIR:-/tmp}/deadboot-appimage.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
mkdir -p -- "$root/dist"

# 1. Newest python-appimage build. Releases are tagged python3.N; assets are named
#    python3.N.x-cp3N-cp3N-manylinux_2_28_x86_64.AppImage (2_28 matches PyQt6's wheels).
printf '%s\n' "Finding the newest portable Python"
curl -fsSL -H 'Accept: application/vnd.github+json' \
    'https://api.github.com/repos/niess/python-appimage/releases?per_page=100' >"$work/releases.json"
py_url=$(python3 - "$work/releases.json" "$arch" <<'PY'
import json, re, sys
rels = json.load(open(sys.argv[1]))
arch = sys.argv[2]
best = None
tags = {"manylinux_2_28": 2, "manylinux2014": 1}   # prefer 2_28; pip still takes PyQt6's 2_28 wheels on 2014
for rel in rels:
    m = re.fullmatch(r"python3\.(\d+)", rel.get("tag_name", ""))
    if not m:
        continue
    for a in rel.get("assets", []):
        n = a["name"]
        if re.search(r"cp3\d+t-", n) or not n.endswith(f"_{arch}.AppImage"):
            continue  # skip free-threaded builds and other arches
        rank = next((v for t, v in tags.items() if f"-{t}_{arch}" in n), 0)
        if not rank:
            continue
        key = (int(m.group(1)), rank, n)
        if best is None or key > best[0]:
            best = (key, a["browser_download_url"])
if not best:
    sys.exit("no python-appimage build found for " + arch)
print(best[1])
PY
)
printf 'Using %s\n' "${py_url##*/}"
curl -fsSL -o "$work/python.AppImage" "$py_url"
chmod +x "$work/python.AppImage"
(cd "$work" && APPIMAGE_EXTRACT_AND_RUN=1 ./python.AppImage --appimage-extract >/dev/null)
appdir="$work/AppDir"
mv -- "$work/squashfs-root" "$appdir"

# The python-appimage layout is opt/python3.N/bin/python3.N.
py=""
for p in "$appdir"/opt/python3.*/bin/python3.*; do
    case "$p" in
        *-config) ;;
        *) [ -x "$p" ] && py=$p ;;
    esac
done
[ -n "$py" ] || die "could not find the bundled Python"
pyrel=${py#"$appdir"/}

# 2. Python dependencies into the bundled interpreter. PyQt6 is abi3, so any new Python works.
"$py" -m pip install --no-warn-script-location --upgrade pip >/dev/null
"$py" -m pip install --no-warn-script-location -r "$root/requirements.txt" certifi

# 3. The app itself, plus its data folder next to the package (hostplatform.resource_dir expects that).
mkdir -p -- "$appdir/opt/deadboot"
cp -R -- "$root/deadboot" "$root/data" "$appdir/opt/deadboot/"
find "$appdir/opt/deadboot" -name '__pycache__' -type d -prune -exec rm -rf -- {} +

# 4. libxcb-cursor is needed by Qt's xcb plugin and is missing on some distros. Bundle the runner's copy.
mkdir -p -- "$appdir/usr/lib"
for lib in /usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0*; do
    [ -e "$lib" ] && cp -L -- "$lib" "$appdir/usr/lib/"
done

# 5. AppRun, desktop entry and icon.
rm -f -- "$appdir"/*.desktop "$appdir"/AppRun "$appdir"/.DirIcon "$appdir"/python*.png "$appdir"/python*.svg
cat >"$appdir/AppRun" <<EOF
#!/usr/bin/env bash
# deADBoot AppRun
# The AppImage runtime sets APPDIR; fall back to the script folder when run from an extracted AppDir.
here=\${APPDIR:-\$(cd -- "\$(dirname -- "\$0")" && pwd)}
export PYTHONPATH="\$here/opt/deadboot"
export LD_LIBRARY_PATH="\${LD_LIBRARY_PATH:+\$LD_LIBRARY_PATH:}\$here/usr/lib"
# The bundled Python's OpenSSL may not know where this distro keeps its CA bundle.
if [ -z "\${SSL_CERT_FILE:-}" ]; then
    for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem \\
             /etc/ssl/cert.pem; do
        if [ -f "\$f" ]; then
            export SSL_CERT_FILE="\$f"
            break
        fi
    done
fi
if [ -z "\${SSL_CERT_FILE:-}" ]; then
    SSL_CERT_FILE=\$("\$here/$pyrel" -s -c 'import certifi; print(certifi.where())')
    export SSL_CERT_FILE
fi
exec "\$here/$pyrel" -s -m deadboot "\$@"
EOF
chmod +x "$appdir/AppRun"
cp -- "$root/data/deadboot.svg" "$appdir/deadboot.svg"
cp -- "$root/data/deadboot.svg" "$appdir/.DirIcon"
cat >"$appdir/deadboot.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=deADBoot
GenericName=Android device utility
Comment=ADB file transfer, app install, screen mirroring, data rescue, flashing and root
Exec=deadboot
Icon=deadboot
Categories=Utility;Development;
Terminal=false
EOF

# 6. Smoke test the AppDir before packing: loads Qt, every theme and every ROM profile.
QT_QPA_PLATFORM=offscreen "$appdir/AppRun" --self-test

# 7. Pack.
curl -fsSL -o "$work/appimagetool" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$arch.AppImage"
chmod +x "$work/appimagetool"
out="$root/dist/deADBoot-$arch.AppImage"
ARCH=$arch APPIMAGE_EXTRACT_AND_RUN=1 VERSION="$version" "$work/appimagetool" --no-appstream "$appdir" "$out"
(cd "$root/dist" && sha256sum -- "${out##*/}" >"${out##*/}.sha256")
printf 'Built %s\n' "$out"
