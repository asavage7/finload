use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

fn find_mpv_dll(target: &str) -> Option<String> {
    if !target.contains("windows") {
        return None; // Linux: rely on system libmpv; declared as a .deb dependency in tauri.conf.json
    }

    // The vendored windows lib is authorative.
    //
    // Must be absolute: finload.spec resolves a relative binary path against
    // its own directory (src-backend/), not against build.rs's cwd
    // (src-tauri/), so a relative path here silently looks in the wrong tree.
    let vendored = Path::new("binaries/vendor/mpv/libmpv-2.dll");
    if vendored.exists() {
        let absolute = env::current_dir().unwrap().join(vendored);
        return Some(absolute.to_string_lossy().into_owned());
    }

    // Fallback for a machine that hasn't run the vendoring script
    let dirs = [
        r"C:\Program Files\MPV Player",
        r"C:\Program Files\mpv",
        r"C:\Program Files (x86)\mpv",
        r"C:\ProgramData\chocolatey\lib\mpv.install\tools",
        r"C:\ProgramData\scoop\apps\mpv\current",
    ];
    let names = ["mpv-2.dll", "libmpv-2.dll", "mpv-1.dll"];
    for dir in dirs {
        for name in names {
            let path = Path::new(dir).join(name);
            if path.exists() {
                return Some(path.to_string_lossy().into_owned());
            }
        }
    }

    // Last resort: whatever's on PATH.
    for name in names {
        if let Ok(output) = Command::new("where").arg(name).output() {
            if output.status.success() {
                let s = String::from_utf8_lossy(&output.stdout);
                if let Some(line) = s.lines().next() {
                    let p = line.trim().to_string();
                    if Path::new(&p).exists() {
                        return Some(p);
                    }
                }
            }
        }
    }
    None
}

fn main() {
    let target = env::var("TARGET").unwrap();

    let out_dir = Path::new("binaries");
    // No target-triple suffix: that naming is an externalBin requirement, and
    // the onedir bundle ships as a bundle resource instead (see tauri.conf.json).
    let binary_name = "python-backend".to_string();
    let exe_ext = if target.contains("windows") {
        ".exe"
    } else {
        ""
    };
    // PyInstaller's COLLECT writes binaries/backend/<exe> plus _internal/ beside
    // it; the executable is what tells us the bundle is already built.
    let target_binary_path = out_dir
        .join("backend")
        .join(format!("{}{}", binary_name, exe_ext));

    if !out_dir.exists() {
        fs::create_dir_all(out_dir).unwrap();
    }

    println!("cargo:rerun-if-env-changed=FINLOAD_TRIM_MPV");
    // Without these, the "rerun-if-env-changed" directive above is the *only*
    // rerun trigger Cargo knows about, so it caches build.rs's output and
    // silently skips re-running it -- meaning a Python-only edit doesn't
    // reach the bundle until something forces a rerun (touching build.rs, a
    // clean build). Listed individually, not the whole src-backend/ root, so
    // .venv/ churn doesn't trigger a PyInstaller rebuild on every touch.
    for entry in [
        "core",
        "providers",
        "routers",
        "services",
        "main.py",
        "finload.spec",
        "requirements.txt",
    ] {
        println!("cargo:rerun-if-changed=../src-backend/{entry}");
    }
    let skip = env::var("SKIP_PYINSTALLER")
        .map(|v| v == "1" || v.to_lowercase() == "true")
        .unwrap_or(false);

    // Rebuild Python backend if binary doesn't exist, or during dev/release (unless explicitly skipped)
    // This ensures Python changes are always picked up during development
    if !target_binary_path.exists() || !skip {
        println!("cargo:warning=Building Python backend with PyInstaller...");

        let pyinstaller_path = if target.contains("windows") {
            "../src-backend/.venv/Scripts/pyinstaller.exe"
        } else {
            "../src-backend/.venv/bin/pyinstaller"
        };

        let mut cmd = Command::new(pyinstaller_path);
        cmd.args([
            "--distpath",
            "binaries",
            "--noconfirm",
            "../src-backend/finload.spec",
        ])
        .env("FINLOAD_BINARY_NAME", &binary_name);

        // By default the sidecar bundles libmpv's full GUI/codec dependency
        // closure, since that's the only variant that works everywhere,
        // including portable targets like AppImage that must run on systems
        // with no libmpv installed at all. deb/rpm builds declare libmpv2 as
        // a package dependency (see tauri.conf.json), so they don't need the
        // bundled copy; set FINLOAD_TRIM_MPV when building just those targets
        // to strip it and rely on the system's libmpv.so.2 instead — see
        // finload.spec for the stripping logic. Never set it for a build that
        // also produces an AppImage in the same invocation.
        if let Ok(trim) = env::var("FINLOAD_TRIM_MPV") {
            cmd.env("FINLOAD_TRIM_MPV", trim);
        }

        // On Windows, find and bundle mpv-2.dll (required by python-mpv at runtime).
        if target.contains("windows") {
            match find_mpv_dll(&target) {
                Some(dll_path) => {
                    println!("cargo:warning=Bundling mpv-2.dll from: {}", dll_path);
                    cmd.env("FINLOAD_MPV_DLL", &dll_path);
                }
                None => {
                    println!("cargo:warning=mpv-2.dll not found — audio will not work in the bundled app.");
                    println!(
                        "cargo:warning=Run: npm run vendor:mpv-windows  (then re-run the build)"
                    );
                }
            }
        }

        let status = cmd.status().expect(
            "Failed to execute PyInstaller. \
             Ensure the venv exists: cd src-backend && python -m venv .venv && pip install -r requirements.txt",
        );

        if !status.success() {
            panic!("PyInstaller failed to compile the Python backend.");
        }

        let _ = fs::remove_dir_all("build");

        drop_vendored_lib_symlinks(&out_dir.join("backend").join("_internal"));
    }

    tauri_build::build();
}

/// Directories whose contents resolve without help from the bundle root.
///
/// Everything numpy and scipy vendor is reachable by RPATH: their extension
/// modules carry `$ORIGIN/../../<pkg>.libs`, and the libraries in there (OpenBLAS
/// depending on libgfortran, say) carry `$ORIGIN`. So the root-level entries for
/// those are redundant.
///
/// `pillow.libs` is deliberately not in this list. Its members have *no* RPATH at
/// all -- `libtiff` NEEDs `libzstd` and can only find it through the loader's
/// default search path, which under PyInstaller means the bundle root. Removing
/// its root symlinks breaks Pillow's TIFF/WebP support at runtime and makes
/// linuxdeploy's dependency walk fail outright when building an AppImage.
const SELF_RESOLVING_LIB_DIRS: [&str; 2] = ["numpy.libs", "scipy.libs"];

/// Delete the redundant symlinks PyInstaller leaves at the root of `_internal/`.
///
/// numpy and scipy vendor their own ~25MB OpenBLAS. PyInstaller's dependency scan
/// wants those libraries at the bundle root too, but links rather than copies
/// them, so its own output carries one copy of each. Tauri's resource collection
/// then copies the tree *dereferencing* symlinks, turning each link back into a
/// full file and adding ~49MB to every bundle.
///
/// Only symlinks are removed, and only those resolving into a directory listed in
/// `SELF_RESOLVING_LIB_DIRS`, so a real library at the root, or a link something
/// actually depends on finding there, is left alone.
fn drop_vendored_lib_symlinks(internal_dir: &Path) {
    let Ok(entries) = fs::read_dir(internal_dir) else {
        return;
    };

    let mut freed = 0u64;
    for entry in entries.flatten() {
        let path = entry.path();
        // symlink_metadata does not follow the link, which is what distinguishes
        // a link from a real file here.
        let Ok(meta) = fs::symlink_metadata(&path) else {
            continue;
        };
        if !meta.file_type().is_symlink() {
            continue;
        }
        let Ok(dest) = fs::read_link(&path) else {
            continue;
        };
        if !dest
            .components()
            .any(|c| SELF_RESOLVING_LIB_DIRS.contains(&c.as_os_str().to_string_lossy().as_ref()))
        {
            continue;
        }
        freed += fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let _ = fs::remove_file(&path);
    }

    if freed > 0 {
        println!(
            "cargo:warning=Dropped vendored-library symlinks from the backend bundle, \
             saving ~{}MB once Tauri copies it",
            freed / 1_048_576
        );
    }
}
