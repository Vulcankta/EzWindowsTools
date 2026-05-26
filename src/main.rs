use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use ezwindows_tools::auto_start;
use ezwindows_tools::config::manager::ConfigManager;
use ezwindows_tools::gui::{SettingsEvent, open_settings};
use ezwindows_tools::i18n::I18n;
use ezwindows_tools::module::manager::ModuleManager;
use ezwindows_tools::tray::{TrayManager, TrayEvent};
use ezwindows_tools::win32::power::{PowerManager, WindowsPowerManager};
use ezwindows_tools::win32::display::DisplayManager;

fn main() -> anyhow::Result<()> {
    // ── 日志初始化 ──
    fern::Dispatch::new()
        .format(|out, message, record| {
            out.finish(format_args!(
                "[{} {}] {}",
                chrono::Local::now().format("%H:%M:%S"),
                record.level(),
                message
            ))
        })
        .level(log::LevelFilter::Info)
        .chain(std::io::stdout())
        .chain({
            let result: Box<dyn std::io::Write + Send + 'static> = match fern::log_file("ezwindows-tools.log") {
                Ok(file) => Box::new(file),
                Err(e1) => {
                    eprintln!("警告: 无法打开日志文件 ({}), 尝试创建...", e1);
                    match std::fs::File::create("ezwindows-tools.log") {
                        Ok(file) => Box::new(file),
                        Err(e2) => {
                            eprintln!("警告: 也无法创建日志文件 ({}), 回退到 stdout", e2);
                            Box::new(std::io::stdout())
                        }
                    }
                }
            };
            result
        })
        .apply()
        .map_err(|e| anyhow::anyhow!("日志初始化失败: {}", e))?;

    log::info!("╔══════════════════════════════════╗");
    log::info!("║   EzWindowsTools (Rust) v0.1.0  ║");
    log::info!("╚══════════════════════════════════╝");

    // ── 配置加载 ──
    let config_mgr = ConfigManager::load(Path::new("config.json"));
    log::info!("配置已加载");

    // ── locales ──
    let locales_dir = Path::new("locales").to_path_buf();
    let mut i18n_instance = I18n::load(&locales_dir, "en");
    let lang = config_mgr.get_manager_config().language;
    if lang != "en" {
        i18n_instance.set_language(&lang, &locales_dir);
    }
    let i18n = Arc::new(Mutex::new(i18n_instance));
    {
        let i18n = i18n.lock().unwrap();
        log::info!("本地化已加载: {} ({} 种语言)", i18n.current_lang, i18n.available.len());
    }

    // ── 自启动 ──
    {
        let mgr_config = config_mgr.get_manager_config();
        let current = auto_start::is_auto_start();
        if mgr_config.auto_start != current {
            log::info!("同步自启动状态: {}", if mgr_config.auto_start { "启用" } else { "禁用" });
            let _ = auto_start::set_auto_start(mgr_config.auto_start);
        }
    }

    // ── 模块管理器 ──
    let (notify_tx, notify_rx) = flume::unbounded::<(String, String)>();
    let mut module_mgr = ModuleManager::new(notify_tx);
    module_mgr.start_all_auto(&config_mgr);
    log::info!("模块管理器已就绪");

    // ── 系统托盘 ──
    let (tray_event_tx, tray_event_rx) = flume::unbounded::<TrayEvent>();
    let mut tray = TrayManager::start(tray_event_tx);
    tray.update_status(module_mgr.get_all_status());
    log::info!("系统托盘已启动");

    // ── 设置窗口通信 ──
    let (settings_tx, settings_rx) = flume::unbounded::<SettingsEvent>();

    // ── Win32 信息 ──
    let pm = WindowsPowerManager;
    if let Ok(scheme) = pm.get_active_scheme() {
        log::info!("当前电源方案: {:?}", scheme);
        if let Ok(s) = pm.read_ac_display_timeout(&scheme) {
            log::info!("AC 熄屏超时: {} 秒 ({} 分钟)", s, s / 60);
        }
    }

    let dm = ezwindows_tools::win32::display::WindowsDisplayManager;
    if let Ok(displays) = dm.get_connected_displays() {
        log::info!("已连接显示器: {} 台", displays.len());
        for d in displays {
            log::info!("  {} @ {}Hz ({}x{})", d.friendly_name, d.current_refresh_rate, d.current_width, d.current_height);
        }
    }

    // ── Ctrl+C ──
    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();
    ctrlc::set_handler(move || {
        log::info!("收到退出信号，正在停止...");
        r.store(false, Ordering::SeqCst);
    })
    .map_err(|e| anyhow::anyhow!("设置 Ctrl+C 处理器失败: {}", e))?;

    log::info!("正在运行。按 Ctrl+C 或托盘「退出」退出。");

    // ── 定时刷新托盘（每 10 秒）──
    let mut last_tray_refresh = std::time::Instant::now();
    const TRAY_REFRESH_INTERVAL: std::time::Duration = std::time::Duration::from_secs(10);

    // ── 主循环 ──
    while running.load(Ordering::SeqCst) {
        // 处理托盘事件
        while let Ok(event) = tray_event_rx.try_recv() {
            match event {
                TrayEvent::OpenSettings => {
                    log::info!("打开设置窗口");
                    let config = config_mgr.get_full_config_json();
                    let (lang, available_langs) = {
                        let i18n = i18n.lock().unwrap();
                        let lang = i18n.current_lang.clone();
                        let langs: Vec<(String, String)> = i18n.available.iter()
                            .map(|li| (li.code.clone(), li.name.clone()))
                            .collect();
                        (lang, langs)
                    };
                    let statuses: std::collections::HashMap<String, String> = module_mgr.get_all_status()
                        .into_iter()
                        .map(|(k, v)| (k, v.detail))
                        .collect();
                    open_settings(config, locales_dir.clone(), lang, settings_tx.clone(), available_langs, statuses);
                }
                TrayEvent::ToggleModule(name) => {
                    log::info!("托盘切换模块: {}", name);
                    if module_mgr.is_running(&name) {
                        module_mgr.stop_module(&name);
                        // 持久化 enabled=false
                        config_mgr.set_module_enabled(&name, false);
                    } else {
                        module_mgr.start_module(&name, &config_mgr);
                        // 持久化 enabled=true
                        config_mgr.set_module_enabled(&name, true);
                    }
                    tray.update_status(module_mgr.get_all_status());
                }
                TrayEvent::Quit => {
                    log::info!("托盘请求退出");
                    running.store(false, Ordering::SeqCst);
                }
            }
        }

        // 处理设置窗口事件
        while let Ok(event) = settings_rx.try_recv() {
            match event {
                SettingsEvent::Save(new_config) => {
                    log::info!("设置已保存，应用配置");

                    // 快照旧的模块启停状态（用于决定是否需要启停模块）
                    let old_sg_enabled = config_mgr.is_module_enabled("screen_guardian");
                    let old_rg_enabled = config_mgr.is_module_enabled("refresh_guardian");

                    // 切换语言
                    if let Some(lang) = new_config.pointer("/manager/language").and_then(|v| v.as_str()) {
                        let mut i18n_guard = i18n.lock().unwrap();
                        if lang != i18n_guard.current_lang {
                            i18n_guard.set_language(lang, &locales_dir);
                            log::info!("语言已切换到: {}", lang);
                        }
                    }

                    // 同步自启动
                    if let Some(auto) = new_config.pointer("/manager/auto_start").and_then(|v| v.as_bool()) {
                        let current = auto_start::is_auto_start();
                        if auto != current {
                            log::info!("{} 自启动", if auto { "启用" } else { "禁用" });
                            let _ = auto_start::set_auto_start(auto);
                        }
                    }

                    // 热重载：将新配置推送到运行中的模块
                    if let Some(cfg) = new_config.pointer("/modules/screen_guardian/config") {
                        module_mgr.on_config_changed("screen_guardian", cfg);
                    }
                    if let Some(cfg) = new_config.pointer("/modules/refresh_guardian/config") {
                        module_mgr.on_config_changed("refresh_guardian", cfg);
                    }

                    // 写入配置
                    config_mgr.apply_full_config(new_config);

                    // 根据 enabled 变化启停模块
                    let new_sg_enabled = config_mgr.is_module_enabled("screen_guardian");
                    let new_rg_enabled = config_mgr.is_module_enabled("refresh_guardian");
                    if old_sg_enabled != new_sg_enabled {
                        if new_sg_enabled { module_mgr.start_module("screen_guardian", &config_mgr); }
                        else { module_mgr.stop_module("screen_guardian"); }
                    }
                    if old_rg_enabled != new_rg_enabled {
                        if new_rg_enabled { module_mgr.start_module("refresh_guardian", &config_mgr); }
                        else { module_mgr.stop_module("refresh_guardian"); }
                    }

                    // 刷新托盘
                    tray.update_status(module_mgr.get_all_status());
                }
                SettingsEvent::Cancel => {
                    log::info!("设置已取消");
                }
            }
        }

        // 处理模块通知
        while let Ok((title, msg)) = notify_rx.try_recv() {
            log::info!("[通知] {}: {}", title, msg);
            tray.notify(&title, &msg);
            tray.update_status(module_mgr.get_all_status());
        }

        // 定时刷新托盘
        if last_tray_refresh.elapsed() >= TRAY_REFRESH_INTERVAL {
            tray.update_status(module_mgr.get_all_status());
            last_tray_refresh = std::time::Instant::now();
        }

        std::thread::sleep(std::time::Duration::from_millis(200));
    }

    // ── 退出 ──
    log::info!("正在停止所有模块...");
    module_mgr.stop_all();
    log::info!("正在停止系统托盘...");
    tray.stop();
    log::info!("已退出。");
    Ok(())
}
