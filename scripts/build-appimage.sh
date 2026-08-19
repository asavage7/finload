#!/usr/bin/env bash
# Builds the AppImage bundle.
#
# This exists because linuxdeploy needs help that the deb and rpm bundlers do
# not. It walks every ELF file in the AppDir and resolves each one's
# dependencies itself, and the onedir Python backend puts roughly 400 shared
# libraries in front of it. PyInstaller keeps the libraries a wheel vendors in
# nested directories (pillow.libs/, numpy.libs/, scipy.libs/) that linuxdeploy's
# search paths do not cover, and it does not honour the RPATH entries that let
# the loader find them at runtime. It aborts on the first one it cannot resolve:
#
#   ERROR: Could not find dependency: libzstd-6ea785c0.so.1.5.7
#
# Pointing LD_LIBRARY_PATH at those directories is enough for it to resolve them.
# The AppDir does not exist yet when this runs; entries that are missing are
# ignored, and it is populated by the time linuxdeploy starts.
#
# None of this applied while the backend was a onefile bundle, because the whole
# payload was sealed inside one executable and linuxdeploy only ever saw that.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
internal="$root/src-tauri/target/release/bundle/appimage/finload.AppDir/usr/lib/finload/backend/_internal"

# Add a directory here if a new dependency starts vendoring its own libraries;
# the symptom is linuxdeploy failing on a name from that package.
vendored=("$internal" "$internal/pillow.libs" "$internal/numpy.libs" "$internal/scipy.libs")

joined=$(IFS=:; echo "${vendored[*]}")
export LD_LIBRARY_PATH="${joined}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# linuxdeploy ships as an AppImage itself, and CI runners have no FUSE mount for
# it to use, so it has to self-extract instead.
export APPIMAGE_EXTRACT_AND_RUN=1

# FINLOAD_TRIM_MPV stays unset: an AppImage has to run where no libmpv is
# installed, so it keeps the full bundled closure. See src-backend/finload.spec.
exec npx tauri build --bundles appimage "$@"
