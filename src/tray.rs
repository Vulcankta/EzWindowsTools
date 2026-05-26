use crate::module::{ModuleStatus, ModuleState};
use std::collections::HashMap;
use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::HBRUSH;
use windows::Win32::System::LibraryLoader::GetModuleHandleA;
use windows::Win32::UI::Shell::*;
use windows::Win32::UI::WindowsAndMessaging::*;

/// 线程安全包装，因为 HWND 可通过整数安全传递
#[derive(Clone, Copy)]
struct SafeHwnd(HWND);
unsafe impl Send for SafeHwnd {}
unsafe impl Sync for SafeHwnd {}

static TOAST_HWND: OnceLock<SafeHwnd> = OnceLock::new();

unsafe extern "system" fn toast_wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    DefWindowProcA(hwnd, msg, wparam, lparam)
}

fn get_toast_hwnd() -> HWND {
    TOAST_HWND.get_or_init(|| {
        unsafe {
            let hinstance: HINSTANCE = GetModuleHandleA(None).unwrap_or_default().into();
            let wc = WNDCLASSEXA {
                cbSize: std::mem::size_of::<WNDCLASSEXA>() as u32,
                style: WNDCLASS_STYLES(0),
                lpfnWndProc: Some(toast_wnd_proc),
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: hinstance,
                hIcon: HICON::default(),
                hCursor: HCURSOR::default(),
                hbrBackground: HBRUSH::default(),
                lpszMenuName: windows::core::PCSTR::null(),
                lpszClassName: windows::core::s!("EzToolsToastClass"),
                hIconSm: HICON::default(),
            };
            let _ = RegisterClassExA(&wc);

            let hwnd = CreateWindowExA(
                WINDOW_EX_STYLE::default(),
                windows::core::s!("EzToolsToastClass"),
                windows::core::s!(""),
                WINDOW_STYLE::default(),
                0,
                0,
                0,
                0,
                None,
                None,
                hinstance,
                None,
            )
            .unwrap_or_default();
            SafeHwnd(hwnd)
        }
    }).0
}

/// 通过 Windows Shell_NotifyIconW 显示 Toast 通知
pub fn show_toast(title: &str, message: &str) {
    fn to_wide(s: &str) -> Vec<u16> {
        OsStr::new(s).encode_wide().chain(std::iter::once(0)).collect()
    }

    unsafe {
        let hwnd = get_toast_hwnd();
        if hwnd.is_invalid() {
            return;
        }

        let mut nid: NOTIFYICONDATAW = std::mem::zeroed();
        nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        nid.hWnd = hwnd;
        nid.uID = 1;
        nid.uFlags = NIF_INFO | NIF_GUID;
        nid.guidItem = windows::core::GUID::from_u128(0x12345678_1234_1234_1234_123456789abc);
        nid.dwInfoFlags = NIIF_INFO;
        nid.Anonymous.uTimeout = 3000;

        let title_wide = to_wide(title);
        let msg_wide = to_wide(message);

        let mut i = 0;
        for &c in title_wide.iter() {
            if i >= 63 {
                break;
            }
            nid.szInfoTitle[i] = c;
            i += 1;
        }
        nid.szInfoTitle[i] = 0;

        i = 0;
        for &c in msg_wide.iter() {
            if i >= 255 {
                break;
            }
            nid.szInfo[i] = c;
            i += 1;
        }
        nid.szInfo[i] = 0;

        let _ = Shell_NotifyIconW(NIM_ADD, &nid);    // 显示通知
        let _ = Shell_NotifyIconW(NIM_DELETE, &nid); // 立即清理
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TrayTheme {
    Ok, Fixed, Error, Init,
}

#[derive(Debug)]
pub enum TrayCommand {
    UpdateStatus(HashMap<String, ModuleStatus>),
    Notify(String, String),
    Quit,
}

/// 托盘→主线程事件
#[derive(Debug)]
pub enum TrayEvent {
    OpenSettings,
    ToggleModule(String),
    Quit,
}

pub struct TrayManager {
    cmd_tx: flume::Sender<TrayCommand>,
    handle: Option<thread::JoinHandle<()>>,
    #[allow(dead_code)]
    current_theme: Arc<Mutex<TrayTheme>>,
}

impl TrayManager {
    pub fn start(event_tx: flume::Sender<TrayEvent>) -> Self {
        let (cmd_tx, cmd_rx) = flume::unbounded::<TrayCommand>();
        let current_theme = Arc::new(Mutex::new(TrayTheme::Init));

        let theme = current_theme.clone();
        let handle = thread::spawn(move || {
            Self::run(cmd_rx, theme, event_tx);
        });

        TrayManager { cmd_tx, handle: Some(handle), current_theme }
    }

    pub fn update_status(&self, statuses: HashMap<String, ModuleStatus>) {
        let _ = self.cmd_tx.send(TrayCommand::UpdateStatus(statuses));
    }

    pub fn notify(&self, title: &str, message: &str) {
        let _ = self.cmd_tx.send(TrayCommand::Notify(title.to_string(), message.to_string()));
    }

    pub fn stop(&mut self) {
        let _ = self.cmd_tx.send(TrayCommand::Quit);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }

    fn run(
        cmd_rx: flume::Receiver<TrayCommand>,
        theme: Arc<Mutex<TrayTheme>>,
        event_tx: flume::Sender<TrayEvent>,
    ) {
        use tray_icon::menu::{Menu, MenuItem, MenuEvent};

        let icon_ok = make_icon(76, 175, 80);
        let icon_fixed = make_icon(255, 193, 7);
        let icon_error = make_icon(244, 67, 54);
        let icon_init = make_icon(176, 176, 176);

        // 初始菜单（只含设置 + 退出，首次 UpdateStatus 后会重建）
        let settings_item = MenuItem::with_id("settings", "设置(S)...", true, None);
        let quit_item = MenuItem::with_id("quit", "退出(X)", true, None);

        let menu = Menu::new();
        menu.append(&settings_item).ok();
        menu.append(&quit_item).ok();

        let tray = tray_icon::TrayIconBuilder::new()
            .with_tooltip("EzWindowsTools")
            .with_icon(icon_init.clone())
            .with_menu(Box::new(menu))
            .build()
            .expect("托盘创建失败");

        let menu_rx = MenuEvent::receiver().clone();

        loop {
            while let Ok(cmd) = cmd_rx.try_recv() {
                match cmd {
                    TrayCommand::UpdateStatus(statuses) => {
                        let new_theme = aggregate_theme(&statuses);
                        let icon = match new_theme {
                            TrayTheme::Ok => icon_ok.clone(),
                            TrayTheme::Fixed => icon_fixed.clone(),
                            TrayTheme::Error => icon_error.clone(),
                            TrayTheme::Init => icon_init.clone(),
                        };
                        *theme.lock().unwrap() = new_theme;
                        let _ = tray.set_icon(Some(icon));
                        let _ = tray.set_tooltip(Some(build_tooltip(&statuses)));

                        // 重建菜单，包含模块列表
                        let new_menu = Menu::new();
                        for (name, st) in &statuses {
                            let prefix = match st.state {
                                ModuleState::Running => "✓ ",
                                ModuleState::Error => "✕ ",
                                ModuleState::Stopped => "○ ",
                            };
                            let item_text = format!("{}{}", prefix, name);
                            let item = MenuItem::with_id(format!("mod_{}", name), &item_text, true, None);
                            new_menu.append(&item).ok();
                        }
                        // 分隔线（用禁用空文本 MenuItem 代替）
                        let sep = MenuItem::with_id("_sep", "", false, None);
                        new_menu.append(&sep).ok();
                        // 设置和退出
                        let settings_item = MenuItem::with_id("settings", "设置(S)...", true, None);
                        let quit_item = MenuItem::with_id("quit", "退出(X)", true, None);
                        new_menu.append(&settings_item).ok();
                        new_menu.append(&quit_item).ok();

                        let _ = tray.set_menu(Some(Box::new(new_menu)));
                    }
                    TrayCommand::Quit => {
                        let _ = event_tx.send(TrayEvent::Quit);
                        return;
                    }
                    TrayCommand::Notify(title, msg) => {
                        show_toast(&title, &msg);
                    }
                }
            }

            while let Ok(event) = menu_rx.try_recv() {
                let id_str: &str = &event.id().0;
                match id_str {
                    "settings" => {
                        let _ = event_tx.send(TrayEvent::OpenSettings);
                    }
                    "quit" => {
                        let _ = event_tx.send(TrayEvent::Quit);
                        return;
                    }
                    _ if id_str.starts_with("mod_") => {
                        if let Some(mod_name) = id_str.strip_prefix("mod_") {
                            let _ = event_tx.send(TrayEvent::ToggleModule(mod_name.to_string()));
                        }
                    }
                    _ => {}
                }
            }

            if let Ok(_event) = tray_icon::TrayIconEvent::receiver().try_recv() {}

            thread::sleep(std::time::Duration::from_millis(100));
        }
    }
}

fn aggregate_theme(statuses: &HashMap<String, ModuleStatus>) -> TrayTheme {
    let mut has_error = false;
    let mut has_fixed = false;
    let mut any_running = false;
    for st in statuses.values() {
        if st.state == ModuleState::Running { any_running = true; }
        if st.state == ModuleState::Error { has_error = true; }
        if st.was_corrected { has_fixed = true; }
    }
    if has_error { TrayTheme::Error }
    else if has_fixed { TrayTheme::Fixed }
    else if any_running { TrayTheme::Ok }
    else { TrayTheme::Init }
}

fn build_tooltip(statuses: &HashMap<String, ModuleStatus>) -> String {
    let mut lines = vec!["EzWindowsTools".to_string()];
    for (_name, st) in statuses {
        let icon = match st.state {
            ModuleState::Running => "●",
            ModuleState::Error => "✕",
            ModuleState::Stopped => "○",
        };
        let detail = if st.detail.is_empty() { "运行中" } else { &st.detail };
        lines.push(format!("  {} {}", icon, detail));
    }
    lines.join("\n")
}

fn make_icon(r: u8, g: u8, b: u8) -> tray_icon::Icon {
    use image::RgbaImage;
    let mut img = RgbaImage::new(64, 64);
    for y in 0..64 {
        for x in 0..64 {
            let dx = x as f32 - 32.0;
            let dy = y as f32 - 32.0;
            if dx * dx + dy * dy <= 26.0 * 26.0 {
                img.put_pixel(x, y, image::Rgba([r, g, b, 255]));
            }
        }
    }
    tray_icon::Icon::from_rgba(img.into_raw(), 64, 64).unwrap_or_else(|e| {
        log::error!("图标创建失败: {}", e);
        // 返回一个 1x1 透明图标作为 fallback
        tray_icon::Icon::from_rgba(vec![0, 0, 0, 0], 1, 1).unwrap_or_else(|_| {
            // 如果连这个都失败，只能 panic
            panic!("无法创建 fallback 图标")
        })
    })
}
