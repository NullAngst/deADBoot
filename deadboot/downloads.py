"""Every network call deADBoot makes lives here.

Fetches APKs (GitHub releases, the F-Droid index), Google's platform-tools and
the Windows scrcpy build, with hash checking. ROMs and factory images are never
fetched: those pages only open in the browser, and the hash helpers here check
the files you downloaded yourself.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, hostplatform

UA = f"deADBoot/{__version__} (+https://github.com/NullAngst/deADBoot)"
FDROID_INDEX = "https://f-droid.org/repo/index-v2.json"
FDROID_REPO = "https://f-droid.org/repo"
REPO_XML = ("https://dl.google.com/android/repository/repository2-3.xml",
            "https://dl.google.com/android/repository/repository2-1.xml")

Progress = Callable[[int, int], None]


class DownloadError(RuntimeError):
    pass


def _request(url: str, headers: dict | None = None) -> urllib.request.Request:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    return urllib.request.Request(url, headers=h)


def get_json(url: str) -> object:
    hdr = {"Accept": "application/vnd.github+json"} if "api.github.com" in url else {}
    try:
        with urllib.request.urlopen(_request(url, hdr), timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface any network failure as one error type
        raise DownloadError(f"{url}: {exc}") from exc


def get_text(url: str) -> str:
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"{url}: {exc}") from exc


def sha256_file(path: Path, progress: Progress | None = None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            h.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    return h.hexdigest()


def normalize_hash(text: str) -> str:
    """Pull a 64-hex SHA-256 out of pasted text ('abc123...  file.zip' works)."""
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return m.group(1).lower() if m else ""


def download(url: str, dest: Path, progress: Progress | None = None,
             expected_sha256: str | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(_request(url), timeout=120) as resp, tmp.open("wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = resp.read(1 << 16)
                if not block:
                    break
                fh.write(block)
                h.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"{url}: {exc}") from exc
    if expected_sha256 and h.hexdigest() != expected_sha256.lower():
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {h.hexdigest()}")
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------- sources

@dataclass
class Fetched:
    path: Path
    version: str
    verification: str        # "project-sha256", "fdroid-sha256", "github-digest", "unverified"
    detail: str = ""

    @property
    def verified_by_project(self) -> bool:
        return self.verification in ("project-sha256", "fdroid-sha256")


def load_sources() -> dict[str, dict]:
    with hostplatform.data_resource("sources.toml").open("rb") as fh:
        return tomllib.load(fh)


def github_latest(owner: str, repo: str) -> dict:
    data = get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    if not isinstance(data, dict) or "assets" not in data:
        raise DownloadError(f"unexpected GitHub API reply for {owner}/{repo}")
    return data


def pick_asset(assets: list[dict], regex: str, abi: str | None = None, abi_assets: bool = False) -> dict | None:
    pattern = re.compile(regex)
    apks = [a for a in assets if pattern.search(a.get("name", ""))]
    if not apks:
        return None
    if abi_assets:
        if abi:
            for a in apks:
                if abi.lower() in a["name"].lower():
                    return a
        known = ("arm64", "armeabi", "x86", "v7a", "v8a")
        universal = [a for a in apks if not any(k in a["name"].lower() for k in known)]
        if universal:
            return universal[0]
    return apks[0]


def _project_checksum(assets: list[dict], asset_name: str) -> str | None:
    """Look for a checksum file the project published next to the asset."""
    for a in assets:
        n = a.get("name", "").lower()
        if n in (asset_name.lower() + ".sha256", asset_name.lower() + ".sha256sum") or \
                n in ("sha256sums", "sha256sums.txt", "checksums.txt", "sha256.txt"):
            try:
                text = get_text(a["browser_download_url"])
            except DownloadError:
                continue
            for line in text.splitlines():
                if asset_name in line or n.startswith(asset_name.lower()):
                    h = normalize_hash(line)
                    if h:
                        return h
    return None


def fetch_github_apk(src: dict, dest_dir: Path, abi: str | None, progress: Progress | None = None) -> Fetched:
    rel = github_latest(src["owner"], src["repo"])
    asset = pick_asset(rel["assets"], src.get("asset_regex", r"\.apk$"), abi, bool(src.get("abi_assets")))
    if asset is None:
        raise DownloadError(f"no APK asset found in {src['owner']}/{src['repo']} {rel.get('tag_name')}")
    expected = _project_checksum(rel["assets"], asset["name"])
    digest = (asset.get("digest") or "")
    gh_hash = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    dest = dest_dir / asset["name"]
    download(asset["browser_download_url"], dest, progress, expected or gh_hash)
    if expected:
        ver, detail = "project-sha256", "matches the checksum the project published"
    elif gh_hash:
        ver, detail = "github-digest", ("matches GitHub's recorded digest for the upload (integrity only, "
                                         "the project publishes no checksum)")
    else:
        ver, detail = "unverified", "the project publishes no checksum; file is unverified"
    return Fetched(dest, rel.get("tag_name", ""), ver, detail)


def fetch_fdroid_apk(package: str, dest_dir: Path, cache: Path, progress: Progress | None = None) -> Fetched:
    cache.mkdir(parents=True, exist_ok=True)
    index_file = cache / "fdroid-index-v2.json"
    download(FDROID_INDEX, index_file)
    with index_file.open("rb") as fh:
        index = json.load(fh)
    pkg = index.get("packages", {}).get(package)
    if not pkg:
        raise DownloadError(f"{package} is not in the F-Droid index")
    versions = list(pkg.get("versions", {}).values())
    if not versions:
        raise DownloadError(f"{package} has no versions in the F-Droid index")
    best = max(versions, key=lambda v: v.get("manifest", {}).get("versionCode", 0))
    file_info = best["file"]
    name = file_info["name"].lstrip("/")
    dest = dest_dir / Path(name).name
    download(f"{FDROID_REPO}/{name}", dest, progress, file_info["sha256"])
    return Fetched(dest, str(best.get("manifest", {}).get("versionName", "")), "fdroid-sha256",
                   "matches the SHA-256 in the F-Droid index")


def fetch_source(key: str, dest_dir: Path, cache: Path, abi: str | None = None,
                 progress: Progress | None = None) -> Fetched:
    sources = load_sources()
    if key not in sources:
        raise DownloadError(f"unknown source {key}")
    src = sources[key]
    if src["kind"] == "fdroid":
        return fetch_fdroid_apk(src["package"], dest_dir, cache, progress)
    return fetch_github_apk(src, dest_dir, abi, progress)


# ---------------------------------------------------------------- platform-tools, scrcpy

def _extract_zip(zip_path: Path, target: Path, strip_top: bool = True) -> None:
    if target.exists():
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory(dir=str(target.parent), ignore_cleanup_errors=True) as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                out = (Path(tmp) / info.filename).resolve()
                if not str(out).startswith(str(Path(tmp).resolve())):
                    raise DownloadError(f"unsafe path in zip: {info.filename}")
            zf.extractall(tmp)
            if not hostplatform.IS_WINDOWS:
                # zipfile drops the executable bit; restore it from the archive's recorded mode.
                for info in zf.infolist():
                    mode = info.external_attr >> 16
                    f = Path(tmp) / info.filename
                    if f.is_file() and mode & 0o111:
                        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        entries = list(Path(tmp).iterdir())
        src = entries[0] if strip_top and len(entries) == 1 and entries[0].is_dir() else Path(tmp)
        if src == Path(tmp):
            shutil.copytree(src, target)
        else:
            shutil.move(str(src), str(target))


def fetch_platform_tools(progress: Progress | None = None) -> Path:
    target = hostplatform.platform_tools_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    zpath = hostplatform.cache_dir() / "platform-tools-latest.zip"
    download(hostplatform.platform_tools_url(), zpath, progress)
    _extract_zip(zpath, target)
    zpath.unlink(missing_ok=True)
    return target


def latest_platform_tools_version() -> tuple[int, ...] | None:
    """Ask Google's SDK repository which platform-tools revision is current."""
    for url in REPO_XML:
        try:
            root = ET.fromstring(get_text(url))
        except (DownloadError, ET.ParseError):
            continue
        for pkg in root.iter():
            if pkg.tag.endswith("remotePackage") and pkg.get("path") == "platform-tools":
                rev = next((c for c in pkg if c.tag.endswith("revision")), None)
                if rev is None:
                    continue
                parts = []
                for name in ("major", "minor", "micro"):
                    node = next((c for c in rev if c.tag.endswith(name)), None)
                    parts.append(int(node.text) if node is not None and node.text else 0)
                return tuple(parts)
    return None


def fetch_scrcpy(progress: Progress | None = None) -> Path:
    pattern = hostplatform.scrcpy_asset_pattern()
    if not pattern:
        raise DownloadError("scrcpy is installed from your distro on Linux")
    rel = github_latest("Genymobile", "scrcpy")
    asset = pick_asset(rel["assets"], pattern)
    if asset is None:
        raise DownloadError("no Windows scrcpy asset in the latest release")
    digest = asset.get("digest") or ""
    expected = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    zpath = hostplatform.cache_dir() / asset["name"]
    download(asset["browser_download_url"], zpath, progress, expected)
    target = hostplatform.scrcpy_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    _extract_zip(zpath, target)
    zpath.unlink(missing_ok=True)
    return target
