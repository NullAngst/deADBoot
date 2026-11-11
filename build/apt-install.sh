#!/usr/bin/env bash
# Installs apt packages on a CI runner, retrying when the package mirrors can't be reached.
# GitHub's runners sometimes fail to resolve Ubuntu's mirrors for a few minutes.
#
# Usage: bash build/apt-install.sh PACKAGE...
set -euo pipefail

[ "$#" -gt 0 ] || { printf '%s\n' "usage: apt-install.sh PACKAGE..." >&2; exit 1; }
for c in sudo apt-get; do
    command -v "$c" >/dev/null 2>&1 || { printf 'missing command: %s\n' "$c" >&2; exit 1; }
done

attempts=5
wait_s=30
i=1
while :; do
    # update can exit 0 with stale indexes when mirrors are down, so the install is what decides.
    if sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update &&
        sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 \
            install -y --no-install-recommends "$@"; then
        exit 0
    fi
    if [ "$i" -ge "$attempts" ]; then
        printf 'apt failed %s times in a row. The package mirrors are unreachable from this runner; re-run the job later.\n' "$attempts" >&2
        exit 1
    fi
    printf 'apt attempt %s of %s failed, waiting %ss\n' "$i" "$attempts" "$wait_s" >&2
    sleep "$wait_s"
    i=$((i + 1))
    wait_s=$((wait_s * 2))
done
