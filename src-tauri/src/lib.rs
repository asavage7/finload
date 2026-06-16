use tauri_plugin_shell::ShellExt;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let sidecar_command = app.shell().sidecar("python-backend").unwrap();
            let (_rx, _child) = sidecar_command
                .spawn()
                .expect("Failed to spawn python backend sidecar");

            println!("Python backend sidecar successfully initiated.");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}