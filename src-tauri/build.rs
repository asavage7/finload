use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

fn main() {
    let target = env::var("TARGET").unwrap();
    let profile = env::var("PROFILE").unwrap();

    let out_dir = Path::new("binaries");
    let binary_name = format!("python-backend-{}", target);
    let exe_ext = if target.contains("windows") {
        ".exe"
    } else {
        ""
    };
    let target_binary_path = out_dir.join(format!("{}{}", binary_name, exe_ext));

    if !out_dir.exists() {
        fs::create_dir_all(out_dir).unwrap();
    }

    if !target_binary_path.exists() || profile == "release" {
        println!("cargo:warning=Building Python backend with PyInstaller...");

        // Define the exact path to the virtual environment pyinstaller
        let pyinstaller_path = "../src-backend/.venv/bin/pyinstaller";

        let status = Command::new(pyinstaller_path)
            .args([
                "--onefile",
                "--name",
                &binary_name,
                "--distpath",
                "binaries",
                "../src-backend/main.py",
            ])
            .status()
            .expect("Failed to execute PyInstaller. Ensure the venv exists at src-backend/.venv");

        if !status.success() {
            panic!("PyInstaller failed to compile the Python backend.");
        }

        let _ = fs::remove_dir_all("build");
        let _ = fs::remove_file(format!("{}.spec", binary_name));
    }

    tauri_build::build();
}
