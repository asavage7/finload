use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

fn find_mpv_dll(target: &str) -> Option<String> {
    if target.contains("windows") {
        // Common install locations for mpv on Windows
        let candidates = [
            r"C:\Program Files\mpv\mpv-2.dll",
            r"C:\Program Files (x86)\mpv\mpv-2.dll",
            r"C:\ProgramData\chocolatey\lib\mpv.install\tools\mpv-2.dll",
            r"C:\ProgramData\scoop\apps\mpv\current\mpv-2.dll",
        ];
        for &path in &candidates {
            if Path::new(path).exists() {
                return Some(path.to_string());
            }
        }
        // Try PATH via `where mpv-2.dll`
        if let Ok(output) = Command::new("where").arg("mpv-2.dll").output() {
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
        None
    } else {
        None // Linux: rely on system libmpv; declared as a .deb dependency in tauri.conf.json
    }
}

fn main() {
    let target = env::var("TARGET").unwrap();
    let profile = env::var("PROFILE").unwrap();

    let out_dir = Path::new("binaries");
    let binary_name = format!("python-backend-{}", target);
    let exe_ext = if target.contains("windows") { ".exe" } else { "" };
    let target_binary_path = out_dir.join(format!("{}{}", binary_name, exe_ext));

    if !out_dir.exists() {
        fs::create_dir_all(out_dir).unwrap();
    }

    let skip = env::var("SKIP_PYINSTALLER").map(|v| v == "1" || v.to_lowercase() == "true").unwrap_or(false);

    // Rebuild Python backend if binary doesn't exist, or during dev/release (unless explicitly skipped)
    // This ensures Python changes are always picked up during development
    if !target_binary_path.exists() || !skip {
        println!("cargo:warning=Building Python backend with PyInstaller...");

        let pyinstaller_path = if target.contains("windows") {
            "../src-backend/.venv/Scripts/pyinstaller.exe"
        } else {
            "../src-backend/.venv/bin/pyinstaller"
        };

        let mut args: Vec<String> = vec![
            "--onefile".into(),
            "--name".into(),
            binary_name.clone(),
            "--distpath".into(),
            "binaries".into(),
            // Include uvicorn/starlette dynamic imports that static analysis misses
            "--collect-all".into(),
            "uvicorn".into(),
            "--collect-all".into(),
            "starlette".into(),
        ];

        // On Windows, find and bundle mpv-2.dll (required by python-mpv at runtime)
        if target.contains("windows") {
            match find_mpv_dll(&target) {
                Some(dll_path) => {
                    println!("cargo:warning=Bundling mpv-2.dll from: {}", dll_path);
                    args.push("--add-binary".into());
                    args.push(format!("{};.", dll_path));
                }
                None => {
                    println!("cargo:warning=mpv-2.dll not found — audio will not work in the bundled app.");
                    println!("cargo:warning=Install mpv via: winget install mpv  (then re-run the build)");
                }
            }
        }

        args.push("../src-backend/main.py".into());

        let status = Command::new(pyinstaller_path)
            .args(&args)
            .status()
            .expect(
                "Failed to execute PyInstaller. \
                 Ensure the venv exists: cd src-backend && python -m venv .venv && pip install -r requirements.txt",
            );

        if !status.success() {
            panic!("PyInstaller failed to compile the Python backend.");
        }

        let _ = fs::remove_dir_all("build");
        let _ = fs::remove_file(format!("{}.spec", binary_name));
    }

    tauri_build::build();
}
