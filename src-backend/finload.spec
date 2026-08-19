# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the finload backend sidecar.

Produces a onedir bundle: ``<distpath>/backend/`` holding the executable and an
``_internal/`` tree beside it. The executable resolves ``_internal`` relative to
its own location, so the directory has to be shipped and launched as a unit --
Tauri carries it as a bundle resource and spawns the inner executable (see
src-tauri/tauri.conf.json and lib.rs).

Invoked by src-tauri/build.rs (not run directly). Env vars configure it:
  FINLOAD_BINARY_NAME  - output executable name (required)
  FINLOAD_MPV_DLL       - path to mpv-2.dll to bundle on Windows (optional)
  FINLOAD_TRIM_MPV     - if set, strip libmpv's bundled dependency closure and
                          rely on the system's libmpv.so.2 instead. Only safe
                          for the deb/rpm targets, which declare libmpv2 as a
                          hard package dependency (see tauri.conf.json) --
                          leave this unset for AppImage (or any target that
                          must run on a system without libmpv2 installed),
                          which is why it stays unset by default.
"""
import os
import re
import subprocess
import sys

from PyInstaller.utils.hooks import collect_all

binary_name = os.environ["FINLOAD_BINARY_NAME"]

# Directory COLLECT writes into, and the name build.rs and tauri.conf.json both
# expect to find under src-tauri/binaries/.
COLLECT_DIR_NAME = "backend"

datas, binaries, hiddenimports = [], [], []
for pkg in ("uvicorn", "starlette"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

mpv_dll = os.environ.get("FINLOAD_MPV_DLL")
if mpv_dll:
    binaries.append((mpv_dll, "."))

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # numba/llvmlite are a hard transitive dependency of librosa (~200MB of
        # native LLVM) but audio_analysis.py never imports the real numba --
        # _numba_shim.py stands in for it, letting librosa's JIT-decorated DSP
        # run as plain Python (validated bit-for-bit identical against
        # real-numba librosa). See _numba_shim.py.
        "numba",
        "llvmlite",
        # Unused transitive weight pulled in by scipy/librosa's optional paths.
        "sklearn",
        "matplotlib",
        "tkinter",
        "pandas",
        "IPython",
        # uvicorn[standard] extras, dropped from requirements.txt but excluded
        # here too so a stale venv that still has them can't put them back.
        # uvicorn selects asyncio/h11 on its own when these are missing.
        "uvloop",
        "httptools",
        "watchfiles",
        "yaml",
    ],
)



def _strip_mpv_gui_closure(analysis):
    """python-mpv loads libmpv.so.2 via ctypes.CDLL(ctypes.util.find_library(...))
    at import time. PyInstaller's binary-dependency scanner auto-detects that
    call and bundles not just libmpv but its *entire* transitive closure -- on
    a typical desktop distro, libmpv.so.2 is built with full GUI/desktop
    support and pulls in X11, Wayland, PulseAudio, JACK, Kerberos, GTK/cairo,
    video encoders (x265/SvtAV1/rav1e), disc playback, and even a TTS engine
    (flite). None of that runs on finload's headless audio-playback path.
    Measured at ~210MB of a ~410MB onedir build.

    We rely on the system's libmpv.so.2 at runtime instead -- already declared
    as a .deb dependency (see tauri.conf.json's linux.deb.depends) -- and strip
    the bundled copy plus everything only reachable through it. A library
    survives if it's also a dependency of some binary outside libmpv's own
    closure (e.g. libssl/libcrypto/libz are needed by Python's stdlib
    regardless of mpv; Pillow and soundfile vendor their own codec libs under
    different filenames, so this never touches those).
    """
    if not sys.platform.startswith("linux"):
        return
    if not os.environ.get("FINLOAD_TRIM_MPV"):
        print("### _strip_mpv_gui_closure: FINLOAD_TRIM_MPV not set, keeping full libmpv closure bundled")
        return

    mpv_entries = [t for t in analysis.binaries if re.match(r"libmpv\.so(\.|$)", t[0])]
    if not mpv_entries:
        return

    def ldd_closure(path):
        seen, stack, closure = set(), [path], {}
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            try:
                out = subprocess.run(["ldd", p], capture_output=True, text=True, check=True).stdout
            except Exception:
                continue
            for line in out.splitlines():
                m = re.match(r"\s*(\S+)\s*=>\s*(/\S+)", line)
                if m:
                    closure[m.group(1)] = m.group(2)
                    stack.append(m.group(2))
        return closure

    mpv_path = mpv_entries[0][1]
    mpv_closure = ldd_closure(mpv_path)
    mpv_closure[mpv_entries[0][0]] = mpv_path

    other_paths = [t[1] for t in analysis.binaries if t[0] not in mpv_closure]
    safe = set()
    for p in other_paths:
        safe.update(ldd_closure(p).keys())

    removable = set(mpv_closure) - safe
    if not removable:
        print("### _strip_mpv_gui_closure: nothing removable, leaving binaries as-is")
        return

    before = len(analysis.binaries)
    analysis.binaries = [t for t in analysis.binaries if t[0] not in removable]
    print(f"### _strip_mpv_gui_closure: dropped {before - len(analysis.binaries)} "
          f"libraries exclusive to bundled libmpv (relying on system libmpv.so.2)")


_strip_mpv_gui_closure(a)

pyz = PYZ(a.pure)

# onedir, not onefile. A onefile build is a self-extracting archive that unpacks
# its entire payload to a fresh temp directory on *every* launch; measured at
# ~200MB and 23 seconds before the first request could be served, with nothing
# cached between runs. It also leaves the temp directory behind when the process
# is killed rather than exiting cleanly, so force-quits accumulate gigabytes.
# COLLECT writes the same payload out once at build time and the executable
# loads straight from it.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=binary_name,
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=COLLECT_DIR_NAME,
)
