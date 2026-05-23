// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// src-tauri/src/main.rs
fn main() {
    // Force WebKitGTK to enable smooth hardware acceleration tracking
    std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "0");
    
    finload_lib::run();
}