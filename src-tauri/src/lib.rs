use std::ffi::c_void;
use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Emitter, Manager};

// Holds the Python backend process so it can be killed on exit. Option so it is
// taken and killed exactly once.
struct BackendProcess(Mutex<Option<std::process::Child>>);

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

#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    // Kill and reap the backend first.
    if let Some(state) = app.try_state::<BackendProcess>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    app.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Run through X11 (XWayland on a Wayland session) rather than natively.
    //
    // GTK3 never implements the xdg-decoration protocol, so a native Wayland
    // window always wears GTK's own client-side titlebar and the compositor is
    // never offered the chance to decorate it. Under X11 the window manager
    // draws its own, which is both the platform-native look and reliable;
    // the native path also proved inconsistent in practice. XWayland keeps the
    // DMA-BUF renderer working, so this costs none of the GPU acceleration that
    // disabling that renderer would, only XWayland's overhead and Wayland
    // niceties like per-monitor fractional scaling.
    //
    // Set only when unset, so `GDK_BACKEND=wayland finload` still forces the
    // native path for anyone who wants it.
    #[cfg(target_os = "linux")]
    if std::env::var_os("GDK_BACKEND").is_none() {
        std::env::set_var("GDK_BACKEND", "x11");
    }

    // Caps the web process rather than letting WebKit's default heuristics grow
    // toward available RAM. Browsing the library streams hundreds of cover
    // images, so the cache genuinely does grow, but the earlier limits (512MB,
    // trimming from 256MB) sat below what a large library needs on screen at
    // once and left it purging and re-decoding images continuously. These are
    // high enough to hold a normal browsing session and still stop a runaway.
    #[cfg(target_os = "linux")]
    {
        use webkit2gtk::{MemoryPressureSettings, WebsiteDataManager};
        if gtk::init().is_ok() {
            let mut settings = MemoryPressureSettings::new();
            settings.set_memory_limit(2048); // MB soft cap for the web process
            settings.set_conservative_threshold(0.7); // begin trimming caches at 70%
            settings.set_strict_threshold(0.9); // aggressive GC + cache purge at 90%
            settings.set_poll_interval(4.0); // seconds between memory checks
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
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            update_now_playing_metadata,
            update_playback_status,
            restart_app,
        ])
        .setup(|app| {
            // The backend is a PyInstaller onedir bundle shipped as a resource,
            // not a Tauri sidecar: the executable resolves its _internal/ tree
            // relative to itself, so the whole directory travels together and is
            // launched in place.
            let backend_exe = app
                .path()
                .resource_dir()
                .expect("failed to resolve the resource directory")
                .join("backend")
                .join(if cfg!(target_os = "windows") {
                    "python-backend.exe"
                } else {
                    "python-backend"
                });

            // Inherited rather than piped: the backend writes its own rotating
            // log file (see src-backend/logging_config.py), so there is no need
            // to drain a pipe to keep it from blocking on a full buffer, and a
            // terminal-launched dev run still shows its output directly.
            let child = std::process::Command::new(&backend_exe)
                .spawn()
                .unwrap_or_else(|e| panic!("failed to spawn the backend at {backend_exe:?}: {e}"));

            // Keep the child alive for the duration of the app; killed on exit.
            app.manage(BackendProcess(Mutex::new(Some(child))));

            // Hide the window until the backend is accepting connections, then
            // show it. This prevents the "backend unavailable" flash on every launch.
            let window = app.get_webview_window("main").expect("no main window");
            window.hide().ok();

            // bundle.icon in tauri.conf.json only feeds the packaged .desktop entry
            // and the hicolor theme, which is what the taskbar and alt-tab resolve.
            // The titlebar draws the window's own icon property, which nothing sets,
            // so it fell back to a generic one. Embedded rather than read from disk
            // so it works the same from a deb, an AppImage or a dev build.
            match tauri::image::Image::from_bytes(include_bytes!("../icons/128x128.png")) {
                Ok(icon) => {
                    let _ = window.set_icon(icon);
                }
                Err(e) => eprintln!("[icon] failed to decode the window icon: {e:?}"),
            }

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

            // DocumentViewer is WebKit's minimal cache model, meant for a viewer
            // that shows one document and never returns to it. It keeps RSS down
            // but makes every revisit re-fetch and re-decode, which is the wrong
            // trade for a library the user scrolls back and forth through.
            // WebBrowser keeps a normal cache; the memory-pressure settings above
            // are what bound it now.
            #[cfg(target_os = "linux")]
            {
                use webkit2gtk::{CacheModel, WebContextExt, WebViewExt};
                let _ = window.with_webview(|webview| {
                    let wv = webview.inner();
                    if let Some(ctx) = wv.web_context() {
                        ctx.set_cache_model(CacheModel::WebBrowser);
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
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        // Reaped so it can't linger as a zombie if the app is
                        // still running its own shutdown work.
                        let _ = child.wait();
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
