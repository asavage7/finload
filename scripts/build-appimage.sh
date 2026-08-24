#!/usr/bin/env bash
# Builds the AppImage bundle.
#
# AppImages need slightly more work than deb/rpm because they bundles all dependencies.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
internal="$root/src-tauri/target/release/bundle/appimage/finload.AppDir/usr/lib/finload/backend/_internal"

# Add a directory here if a new dependency starts vendoring its own libraries
vendored=("$internal" "$internal/pillow.libs" "$internal/numpy.libs" "$internal/scipy.libs")

joined=$(IFS=:; echo "${vendored[*]}")
export LD_LIBRARY_PATH="${joined}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# linuxdeploy ships as an AppImage itself, and CI runners have no FUSE mount for
# it to use, so it has to self-extract instead.
export APPIMAGE_EXTRACT_AND_RUN=1
exec npx tauri build --bundles appimage "$@"
