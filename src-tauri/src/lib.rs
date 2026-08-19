use std::ffi::c_void;
use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

// Holds the Python sidecar so it can be killed on exit. Option so it can be
// taken out and consumed by CommandChild::kill() exactly once.
struct BackendProcess(Mutex<Option<CommandChild>>);

// Holds the OS media-session handle (MPRIS/SMTC/Now Playing). None if souvlaki
// failed to initialize (e.g. no D-Bus session) — commands become no-ops rather
// than failing the whole app.
struct MediaControlsState(Mutex<Option<souvlaki::MediaControls>>);

// souvlaki::MediaControlEvent isn't Serialize, so it's translated into this
// shape before being emitted to the frontend as a Tauri event.
#[derive(Clone, serde::Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum MediaControlEventPayload {
    Play,
    Pause,
    Toggle,
    Next,
    Previous,
    Stop,
    Seek { direction: String },
    SeekBy { direction: String, secs: f64 },
    SetPosition { secs: f64 },
    SetVolume { value: f64 },
    OpenUri { uri: String },
    Quit,
}

impl MediaControlEventPayload {
    // Raise is handled directly in the attach() callback (it's a "show the
    // window" request, not a playback action), so it never reaches here.
    fn from_event(event: souvlaki::MediaControlEvent) -> Option<Self> {
        use souvlaki::{MediaControlEvent as E, SeekDirection};
        let seek_dir_str = |d: SeekDirection| match d {
            SeekDirection::Forward => "forward".to_string(),
            SeekDirection::Backward => "backward".to_string(),
        };
        Some(match event {
            E::Play => Self::Play,
            E::Pause => Self::Pause,
            E::Toggle => Self::Toggle,
            E::Next => Self::Next,
            E::Previous => Self::Previous,
            E::Stop => Self::Stop,
            E::Seek(dir) => Self::Seek {
                direction: seek_dir_str(dir),
            },
            E::SeekBy(dir, dur) => Self::SeekBy {
                direction: seek_dir_str(dir),
                secs: dur.as_secs_f64(),
            },
            E::SetPosition(pos) => Self::SetPosition {
                secs: pos.0.as_secs_f64(),
            },
            E::SetVolume(v) => Self::SetVolume { value: v },
            E::OpenUri(uri) => Self::OpenUri { uri },
            E::Quit => Self::Quit,
            E::Raise => return None,
        })
    }
}

#[tauri::command]
fn update_now_playing_metadata(
    state: tauri::State<MediaControlsState>,
    title: Option<String>,
    artist: Option<String>,
    album: Option<String>,
    duration_secs: Option<f64>,
    cover_url: Option<String>,
) {
    if let Some(controls) = state.0.lock().unwrap().as_mut() {
        let _ = controls.set_metadata(souvlaki::MediaMetadata {
            title: title.as_deref(),
            artist: artist.as_deref(),
            album: album.as_deref(),
            cover_url: cover_url.as_deref(),
            duration: duration_secs.map(Duration::from_secs_f64),
        });
    }
}

#[tauri::command]
fn update_playback_status(
    state: tauri::State<MediaControlsState>,
    is_paused: bool,
    position_secs: f64,
) {
    if let Some(controls) = state.0.lock().unwrap().as_mut() {
        let progress = Some(souvlaki::MediaPosition(Duration::from_secs_f64(
            position_secs.max(0.0),
        )));
        let playback = if is_paused {
            souvlaki::MediaPlayback::Paused { progress }
        } else {
            souvlaki::MediaPlayback::Playing { progress }
        };
        let _ = controls.set_playback(playback);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
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

    let builder = tauri::Builder::default();

    // Must be the first plugin registered (Tauri requirement). Without this,
    // launching a second instance would spawn a second souvlaki media-session
    // handle trying to claim the same MPRIS D-Bus name as the first, which
    // panics on souvlaki's background D-Bus thread — fatal under this crate's
    // `panic = "abort"` release profile. Focus the existing window instead.
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.set_focus();
        }
    }));

    builder
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            update_now_playing_metadata,
            update_playback_status,
        ])
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

            // OS media-session integration (MPRIS on Linux, SMTC on Windows,
            // Now Playing on macOS). hwnd is only meaningful on Windows, where
            // souvlaki needs it to hook into SystemMediaTransportControls.
            let hwnd: Option<*mut c_void> = {
                #[cfg(target_os = "windows")]
                {
                    Some(window.hwnd().expect("failed to get window hwnd").0 as *mut c_void)
                }
                #[cfg(not(target_os = "windows"))]
                {
                    None
                }
            };
            let media_config = souvlaki::PlatformConfig {
                dbus_name: "com.finload.Finload",
                display_name: "Finload",
                hwnd,
            };
            match souvlaki::MediaControls::new(media_config) {
                Ok(mut controls) => {
                    let media_app_handle = app.handle().clone();
                    if let Err(e) = controls.attach(move |event: souvlaki::MediaControlEvent| {
                        // "Raise" means "bring the app to the foreground" — handle it
                        // directly instead of round-tripping through the frontend.
                        if matches!(event, souvlaki::MediaControlEvent::Raise) {
                            if let Some(w) = media_app_handle.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                            return;
                        }
                        if let Some(payload) = MediaControlEventPayload::from_event(event) {
                            if let Err(e) = media_app_handle.emit("media-control", payload) {
                                eprintln!("[media-controls] failed to emit event to frontend: {e}");
                            }
                        }
                    }) {
                        eprintln!("[media-controls] failed to attach event handler: {e:?}");
                    }
                    app.manage(MediaControlsState(Mutex::new(Some(controls))));
                }
                Err(e) => {
                    eprintln!("[media-controls] failed to initialize OS media controls: {e:?}");
                    app.manage(MediaControlsState(Mutex::new(None)));
                }
            }

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
                if let Some(state) = app_handle.try_state::<MediaControlsState>() {
                    if let Some(mut controls) = state.0.lock().unwrap().take() {
                        let _ = controls.detach();
                    }
                }
            }
        });
}
