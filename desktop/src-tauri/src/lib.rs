use tauri::{
    image::Image,
    menu::MenuBuilder,
    tray::TrayIconBuilder,
    AppHandle, Manager,
};
use tauri_plugin_fs::FsExt;
use tauri_plugin_notification::NotificationExt;

const TRAY_ID: &str = "f1-tray";
const TRAY_ICON_SIZE: u32 = 32;
const TRAY_ICON_GREY: [u8; 4] = [128, 128, 128, 255];
const TRAY_ICON_GREEN: [u8; 4] = [34, 197, 94, 255];

// Built at runtime from raw RGBA bytes rather than loading a PNG asset —
// a solid-color square is all the tray needs for the active/idle indicator.
fn solid_color_icon(rgba: [u8; 4]) -> Image<'static> {
    let mut pixels = Vec::with_capacity((TRAY_ICON_SIZE * TRAY_ICON_SIZE * 4) as usize);
    for _ in 0..(TRAY_ICON_SIZE * TRAY_ICON_SIZE) {
        pixels.extend_from_slice(&rgba);
    }
    Image::new_owned(pixels, TRAY_ICON_SIZE, TRAY_ICON_SIZE)
}

fn show_overlay_window(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("overlay") {
        window.set_always_on_top(true)?;
        window.show()?;
    }
    Ok(())
}

fn hide_overlay_window(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("overlay") {
        window.hide()?;
    }
    Ok(())
}

#[tauri::command]
fn show_overlay(app: AppHandle) -> Result<(), String> {
    show_overlay_window(&app).map_err(|e| e.to_string())
}

#[tauri::command]
fn hide_overlay(app: AppHandle) -> Result<(), String> {
    hide_overlay_window(&app).map_err(|e| e.to_string())
}

#[tauri::command]
fn set_tray_status(app: AppHandle, status: bool) -> Result<(), String> {
    let icon = solid_color_icon(if status { TRAY_ICON_GREEN } else { TRAY_ICON_GREY });
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        tray.set_icon(Some(icon)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

// Grants the fs plugin write access to exactly the path the user just chose
// via the native save dialog — the fs plugin's default scope does not cover
// arbitrary user-picked paths, and a broad static scope (e.g. $HOME/**)
// would be far more access than a single CSV export needs.
#[tauri::command]
fn allow_csv_export_path(app: AppHandle, path: String) -> Result<(), String> {
    app.fs_scope().allow_file(&path).map_err(|e| e.to_string())
}

#[tauri::command]
fn send_threat_notification(app: AppHandle, driver: String, message: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(format!("Undercut threat — {driver}"))
        .body(message)
        .show()
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let menu = MenuBuilder::new(app)
                .text("open_app", "Open App")
                .text("show_overlay", "Show Overlay")
                .text("hide_overlay", "Hide Overlay")
                .separator()
                .text("quit", "Quit")
                .build()?;

            TrayIconBuilder::with_id(TRAY_ID)
                .icon(solid_color_icon(TRAY_ICON_GREY))
                .menu(&menu)
                .tooltip("F1 Strategy Engine")
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open_app" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "show_overlay" => {
                        let _ = show_overlay_window(app);
                    }
                    "hide_overlay" => {
                        let _ = hide_overlay_window(app);
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            show_overlay,
            hide_overlay,
            set_tray_status,
            allow_csv_export_path,
            send_threat_notification,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
