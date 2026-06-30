use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

// Holds the Python sidecar so it can be killed on exit. Option so it can be
// taken out and consumed by CommandChild::kill() exactly once.
struct BackendProcess(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // WebKitGTK: the DMABUF renderer is broken on a number of Linux GPU/driver
    // setups, which shows up as sluggish rendering and compositing artifacts.
    // Disabling it lets WebKit fall back to a path that composites correctly.
    // Must be set before the webview is created.
    #[cfg(target_os = "linux")]
    std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");

    #[cfg(target_os = "linux")]
    {
        use webkit2gtk::{MemoryPressureSettings, WebsiteDataManager};
        if gtk::init().is_ok() {
            let mut settings = MemoryPressureSettings::new();
            settings.set_memory_limit(512); // MB soft cap for the web process
            settings.set_conservative_threshold(0.5); // begin trimming caches at 50%
            settings.set_strict_threshold(0.75); // aggressive GC + cache purge at 75%
            settings.set_poll_interval(2.0); // seconds between memory checks
            WebsiteDataManager::set_memory_pressure_settings(&mut settings);
        }
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let sidecar = app
                .shell()
                .sidecar("python-backend")
                .expect("failed to create python-backend sidecar command");

            let (rx, child) = sidecar
                .spawn()
                .expect("failed to spawn python-backend sidecar");

            // Keep the child alive for the duration of the app; killed on exit.
            app.manage(BackendProcess(Mutex::new(Some(child))));

            // Drain stdout/stderr so the sidecar never blocks on a full pipe buffer.
            tauri::async_runtime::spawn(async move {
                let mut rx = rx;
                while let Some(event) = rx.recv().await {
                    match event {
                        tauri_plugin_shell::process::CommandEvent::Stdout(line) => {
                            print!("[backend] {}", String::from_utf8_lossy(&line));
                        }
                        tauri_plugin_shell::process::CommandEvent::Stderr(line) => {
                            eprint!("[backend] {}", String::from_utf8_lossy(&line));
                        }
                        _ => {}
                    }
                }
            });

            // Hide the window until the backend is accepting connections, then
            // show it. This prevents the "backend unavailable" flash on every launch.
            let window = app.get_webview_window("main").expect("no main window");
            window.hide().ok();

            // Bound the WebKitGTK resource/image cache. Browsing the library streams
            // hundreds of cover images, and WebKit's default cache model grows its
            // in-memory cache toward a large soft cap (RSS climbs to 500MB+).
            // DocumentViewer minimizes that cache; image bytes are already disk-cached
            // by the Python backend, so re-fetches are cheap.
            #[cfg(target_os = "linux")]
            {
                use webkit2gtk::{CacheModel, WebContextExt, WebViewExt};
                let _ = window.with_webview(|webview| {
                    let wv = webview.inner();
                    if let Some(ctx) = wv.web_context() {
                        ctx.set_cache_model(CacheModel::DocumentViewer);
                    }
                });
            }

            std::thread::spawn(move || {
                let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
                loop {
                    if TcpStream::connect_timeout(&addr, Duration::from_millis(100)).is_ok() {
                        // Port is open; give uvicorn a moment to finish its startup
                        // sequence before the frontend starts firing requests.
                        std::thread::sleep(Duration::from_millis(300));
                        window.show().ok();
                        window.set_focus().ok();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(150));
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Kill the Python sidecar when the app is shutting down so it never
            // lingers as an orphaned uvicorn process.
            if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<BackendProcess>() {
                    if let Some(child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}