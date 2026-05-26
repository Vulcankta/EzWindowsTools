use std::collections::HashMap;
use std::path::PathBuf;

use eframe::egui;

use crate::win32::display::DisplayManager;

/// 设置变更事件
pub enum SettingsEvent {
    Save(serde_json::Value),
    Cancel,
}

/// 在独立线程中打开设置窗口
pub fn open_settings(
    config: serde_json::Value,
    locales_dir: PathBuf,
    current_lang: String,
    event_tx: flume::Sender<SettingsEvent>,
    available_langs: Vec<(String, String)>,
    module_statuses: HashMap<String, String>,
) {
    let locales_dir2 = locales_dir.clone();
    std::thread::spawn(move || {
        let native_options = eframe::NativeOptions {
            viewport: egui::ViewportBuilder::default()
                .with_inner_size([640.0, 560.0])
                .with_min_inner_size([480.0, 360.0])
                .with_visible(true),
            ..Default::default()
        };

        // 获取已连接的显示器列表
        let connected_displays = match crate::win32::display::WindowsDisplayManager.get_connected_displays() {
            Ok(displays) => displays.into_iter().map(|d| RefreshDisplayInfo {
                name: d.name,
                friendly_name: d.friendly_name,
                current_refresh_rate: d.current_refresh_rate,
            }).collect(),
            Err(_) => Vec::new(),
        };

        let app = SettingsApp::new(config, locales_dir2, current_lang, event_tx, connected_displays, available_langs, module_statuses);
        eframe::run_native("EzWindowsTools 设置", native_options, Box::new(|_cc| Ok(Box::new(app)))).ok();
    });
}

#[derive(Clone)]
struct RefreshDisplayInfo {
    name: String,
    friendly_name: String,
    current_refresh_rate: u32,
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct RecordedDisplayEntry {
    expected_refresh_rate: u32,
    enabled: bool,
}

struct SettingsApp {
    config: serde_json::Value,
    locales_dir: PathBuf,
    current_lang: String,
    event_tx: flume::Sender<SettingsEvent>,

    // UI 状态
    available_langs: Vec<(String, String)>,

    // 模块 UI 状态
    module_enabled: HashMap<String, bool>,
    module_auto_start: HashMap<String, bool>,
    module_collapsed: HashMap<String, bool>,

    // 屏幕超时配置
    sg_check_interval: u64,
    sg_ac_display: u64,
    sg_dc_display: u64,
    sg_ac_sleep: u64,
    sg_dc_sleep: u64,
    sg_show_notifications: bool,

    // 刷新率配置
    rg_check_interval: u64,
    rg_show_notifications: bool,

    // 刷新率显示列表
    rg_connected_displays: Vec<RefreshDisplayInfo>,
    rg_recorded_displays: HashMap<String, RecordedDisplayEntry>,

    // 模块状态（打开窗口时的快照）
    module_statuses: HashMap<String, String>,
}

impl SettingsApp {
    fn new(
        config: serde_json::Value,
    #[allow(dead_code)]
    locales_dir: PathBuf,
        current_lang: String,
        event_tx: flume::Sender<SettingsEvent>,
        rg_connected_displays: Vec<RefreshDisplayInfo>,
        available_langs: Vec<(String, String)>,
        module_statuses: HashMap<String, String>,
    ) -> Self {
        // 读取模块配置
        let config_clone = config.clone();
        let modules = config_clone.get("modules");
        let sg = modules.and_then(|m| m.get("screen_guardian"));
        let rg = modules.and_then(|m| m.get("refresh_guardian"));

        let sg_config = sg.and_then(|m| m.get("config"));
        let rg_config = rg.and_then(|m| m.get("config"));

        let mut module_enabled = HashMap::new();
        let mut module_auto_start = HashMap::new();
        let mut module_collapsed = HashMap::new();

        for name in &["screen_guardian", "refresh_guardian"] {
            let info = modules.and_then(|m| m.get(*name));
            module_enabled.insert(name.to_string(), info.and_then(|i| i.get("enabled")).and_then(|v| v.as_bool()).unwrap_or(true));
            module_auto_start.insert(name.to_string(), info.and_then(|i| i.get("auto_start")).and_then(|v| v.as_bool()).unwrap_or(false));
            module_collapsed.insert(name.to_string(), false);
        }

        // 读取屏幕超时配置
        let sg_cfg = |key: &str, default: u64| {
            sg_config.and_then(|c| c.get(key)).and_then(|v| v.as_u64()).unwrap_or(default)
        };
        let rg_cfg = |key: &str, default: u64| {
            rg_config.and_then(|c| c.get(key)).and_then(|v| v.as_u64()).unwrap_or(default)
        };

        // 解析已记录的显示器配置
        let mut rg_recorded_displays: HashMap<String, RecordedDisplayEntry> = HashMap::new();
        if let Some(displays) = config.pointer("/modules/refresh_guardian/config/displays") {
            if let Some(obj) = displays.as_object() {
                for (name, val) in obj {
                    if let Some(entry) = val.as_object() {
                        let expected = entry.get("expected_refresh_rate").and_then(|v| v.as_u64()).unwrap_or(60) as u32;
                        let enabled = entry.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
                        rg_recorded_displays.insert(name.clone(), RecordedDisplayEntry { expected_refresh_rate: expected, enabled });
                    }
                }
            }
        }

        SettingsApp {
            config,
            locales_dir,
            current_lang,
            event_tx,
            available_langs,
            module_enabled,
            module_auto_start,
            module_collapsed,

            sg_check_interval: sg_cfg("check_interval_seconds", 30),
            sg_ac_display: sg_cfg("ac_display_timeout_minutes", 10),
            sg_dc_display: sg_cfg("dc_display_timeout_minutes", 5),
            sg_ac_sleep: sg_cfg("ac_sleep_timeout_minutes", 30),
            sg_dc_sleep: sg_cfg("dc_sleep_timeout_minutes", 15),
            sg_show_notifications: sg_config.and_then(|c| c.get("show_notifications")).and_then(|v| v.as_bool()).unwrap_or(true),

            rg_check_interval: rg_cfg("check_interval_seconds", 60),
            rg_show_notifications: rg_config.and_then(|c| c.get("show_notifications")).and_then(|v| v.as_bool()).unwrap_or(true),

            rg_connected_displays,
            rg_recorded_displays,
            module_statuses,
        }
    }

    fn build_output_config(&self) -> serde_json::Value {
        let mut sg_cfg = serde_json::Map::new();
        sg_cfg.insert("check_interval_seconds".into(), self.sg_check_interval.into());
        sg_cfg.insert("ac_display_timeout_minutes".into(), self.sg_ac_display.into());
        sg_cfg.insert("dc_display_timeout_minutes".into(), self.sg_dc_display.into());
        sg_cfg.insert("ac_sleep_timeout_minutes".into(), self.sg_ac_sleep.into());
        sg_cfg.insert("dc_sleep_timeout_minutes".into(), self.sg_dc_sleep.into());
        sg_cfg.insert("show_notifications".into(), self.sg_show_notifications.into());

        let mut sg_info = serde_json::Map::new();
        sg_info.insert("enabled".into(), (*self.module_enabled.get("screen_guardian").unwrap_or(&true)).into());
        sg_info.insert("auto_start".into(), (*self.module_auto_start.get("screen_guardian").unwrap_or(&false)).into());
        sg_info.insert("config".into(), serde_json::Value::Object(sg_cfg));

        let mut rg_cfg = serde_json::Map::new();
        rg_cfg.insert("check_interval_seconds".into(), self.rg_check_interval.into());
        rg_cfg.insert("show_notifications".into(), self.rg_show_notifications.into());
        // Serialize recorded displays
        let mut displays = serde_json::Map::new();
        for (name, entry) in &self.rg_recorded_displays {
            let mut d = serde_json::Map::new();
            d.insert("expected_refresh_rate".into(), entry.expected_refresh_rate.into());
            d.insert("enabled".into(), entry.enabled.into());
            displays.insert(name.clone(), serde_json::Value::Object(d));
        }
        rg_cfg.insert("displays".into(), serde_json::Value::Object(displays));

        let mut rg_info = serde_json::Map::new();
        rg_info.insert("enabled".into(), (*self.module_enabled.get("refresh_guardian").unwrap_or(&true)).into());
        rg_info.insert("auto_start".into(), (*self.module_auto_start.get("refresh_guardian").unwrap_or(&false)).into());
        rg_info.insert("config".into(), serde_json::Value::Object(rg_cfg));

        let mut modules = serde_json::Map::new();
        modules.insert("screen_guardian".into(), serde_json::Value::Object(sg_info));
        modules.insert("refresh_guardian".into(), serde_json::Value::Object(rg_info));

        let mut output = serde_json::Map::new();
        // manager 配置（含语言 + 自启动）
        let mut manager = serde_json::Map::new();
        if let Some(old_mgr) = self.config.get("manager") {
            if let Some(v) = old_mgr.get("auto_start") {
                manager.insert("auto_start".into(), v.clone());
            } else {
                manager.insert("auto_start".into(), false.into());
            }
        } else {
            manager.insert("auto_start".into(), false.into());
        }
        manager.insert("language".into(), self.current_lang.clone().into());
        output.insert("manager".into(), serde_json::Value::Object(manager));
        output.insert("modules".into(), serde_json::Value::Object(modules));

        serde_json::Value::Object(output)
    }
}

impl eframe::App for SettingsApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                // ── 语言选择 ──
                ui.horizontal(|ui| {
                    ui.label("语言 / Language:");
                    egui::ComboBox::from_id_source("lang_selector")
                        .selected_text(&self.current_lang)
                        .show_ui(ui, |ui| {
                            for (code, name) in &self.available_langs {
                                let label = format!("{} ({})", name, code);
                                if ui.selectable_label(self.current_lang == *code, &label).clicked() {
                                    self.current_lang = code.clone();
                                }
                            }
                        });
                });
                ui.separator();

                // ── 全局设置 ──
                ui.horizontal(|ui| {
                    ui.label("自启动:");
                    let auto_start = self.config.get("manager").and_then(|m| m.get("auto_start")).and_then(|v| v.as_bool()).unwrap_or(false);
                    let mut as_val = auto_start;
                    if ui.checkbox(&mut as_val, "开机自动启动").changed() {
                        // 保存到 config 的 manager 段
                        if let Some(obj) = self.config.as_object_mut() {
                            obj.entry("manager").or_insert_with(|| serde_json::json!({}));
                            if let Some(mgr) = obj.get_mut("manager").and_then(|m| m.as_object_mut()) {
                                mgr.insert("auto_start".into(), as_val.into());
                            }
                        }
                    }
                });
                ui.separator();

                // ── 模块列表 ──
                let module_names = [
                    ("screen_guardian", "屏幕超时守护", "保持显示器超时和睡眠超时在期望值"),
                    ("refresh_guardian", "刷新率守护", "保持各显示器运行在期望的刷新率"),
                ];

                for (name, display_name, description) in module_names {
                    let mut enabled = *self.module_enabled.get(name).unwrap_or(&true);
                    let mut auto_start = *self.module_auto_start.get(name).unwrap_or(&false);
                    let collapsed = *self.module_collapsed.get(name).unwrap_or(&false);

                    egui::Frame::group(ui.style())
                        .inner_margin(egui::Margin::symmetric(8.0, 4.0))
                        .show(ui, |ui| {
                            // ── 标题行 ──
                            ui.horizontal(|ui| {
                                let collapse_label = if collapsed { "▶" } else { "▼" };
                                if ui.button(collapse_label).clicked() {
                                    self.module_collapsed.insert(name.to_string(), !collapsed);
                                }
                                ui.strong(display_name);
                                // 显示模块状态
                                let name_str: &str = name;
                                if let Some(status) = self.module_statuses.get(name_str) {
                                    let (color, text) = if status.contains("失败") || status.contains("error") {
                                        (egui::Color32::RED, status.as_str())
                                    } else if status.contains("修正") {
                                        (egui::Color32::YELLOW, "已修正")
                                    } else if status.contains("正常") || status.contains("运行") {
                                        (egui::Color32::GREEN, status.as_str())
                                    } else {
                                        (egui::Color32::GRAY, status.as_str())
                                    };
                                    ui.label(egui::RichText::new(text).size(11.0).color(color));
                                }
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    ui.checkbox(&mut enabled, "启用");
                                });
                            });
                            ui.label(egui::RichText::new(description).size(11.0).color(egui::Color32::GRAY));

                            // ── 展开内容 ──
                            if !collapsed {
                                ui.add_space(4.0);
                                ui.checkbox(&mut auto_start, "随启动加载");

                                // 模块特定配置
                                match name.as_ref() {
                                    "screen_guardian" => {
                                        ui.add_space(4.0);
                                        ui.horizontal(|ui| {
                                            ui.label("检查间隔(秒):");
                                            ui.add(egui::Slider::new(&mut self.sg_check_interval, 5..=600));
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label("AC 熄屏(分钟):");
                                            ui.add(egui::Slider::new(&mut self.sg_ac_display, 0..=999));
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label("DC 熄屏(分钟):");
                                            ui.add(egui::Slider::new(&mut self.sg_dc_display, 0..=999));
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label("AC 睡眠(分钟):");
                                            ui.add(egui::Slider::new(&mut self.sg_ac_sleep, 0..=999));
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label("DC 睡眠(分钟):");
                                            ui.add(egui::Slider::new(&mut self.sg_dc_sleep, 0..=999));
                                        });
                                        ui.checkbox(&mut self.sg_show_notifications, "显示通知");
                                    }
                                    "refresh_guardian" => {
                                        ui.add_space(4.0);
                                        ui.horizontal(|ui| {
                                            ui.label("检查间隔(秒):");
                                            ui.add(egui::Slider::new(&mut self.rg_check_interval, 5..=600));
                                        });
                                        ui.checkbox(&mut self.rg_show_notifications, "显示通知");

                                        ui.separator();
                                        ui.strong("已连接的显示器");
                                        ui.horizontal(|ui| {
                                            ui.label(format!("{} 台显示器", self.rg_connected_displays.len()));
                                            if ui.button("↻ 刷新").clicked() {
                                                // 重新枚举显示器
                                                 if let Ok(displays) = crate::win32::display::WindowsDisplayManager.get_connected_displays() {
                                                    self.rg_connected_displays = displays.into_iter().map(|d| RefreshDisplayInfo {
                                                        name: d.name,
                                                        friendly_name: d.friendly_name,
                                                        current_refresh_rate: d.current_refresh_rate,
                                                    }).collect();
                                                }
                                            }
                                        });
                                        // Display connected list (scrollable, compact)
                                        egui::ScrollArea::vertical().max_height(120.0).show(ui, |ui| {
                                            for d in &self.rg_connected_displays {
                                                ui.horizontal(|ui| {
                                                    ui.label(format!("{} @ {}Hz", d.friendly_name, d.current_refresh_rate));
                                                });
                                            }
                                            if self.rg_connected_displays.is_empty() {
                                                ui.label("(无)");
                                            }
                                        });

                                        ui.separator();
                                        ui.strong("已记录的显示器");
                                        ui.horizontal(|ui| {
                                            if ui.button("＋ 添加当前").clicked() {
                                                // Add connected displays that aren't already recorded
                                                for cd in &self.rg_connected_displays.clone() {
                                                    self.rg_recorded_displays.entry(cd.name.clone()).or_insert(RecordedDisplayEntry {
                                                        expected_refresh_rate: cd.current_refresh_rate,
                                                        enabled: true,
                                                    });
                                                }
                                            }
                                            if ui.button("清空").clicked() {
                                                self.rg_recorded_displays.clear();
                                            }
                                        });

                                        // Display recorded list with controls
                                        egui::ScrollArea::vertical().max_height(200.0).show(ui, |ui| {
                                            let mut to_remove: Option<String> = None;
                                            let names: Vec<String> = self.rg_recorded_displays.keys().cloned().collect();
                                            for name in &names {
                                                ui.horizontal(|ui| {
                                                    let mut entry = self.rg_recorded_displays.get(name).cloned().unwrap_or(RecordedDisplayEntry { expected_refresh_rate: 60, enabled: true });
                                                    let mut enabled = entry.enabled;
                                                    ui.checkbox(&mut enabled, "");
                                                    entry.enabled = enabled;
                                                    ui.label(name);
                                                    // Simple refresh rate input
                                                    ui.add(egui::DragValue::new(&mut entry.expected_refresh_rate).suffix("Hz").range(24..=360));
                                                    if ui.button("✕").clicked() {
                                                        to_remove = Some(name.clone());
                                                    }
                                                    self.rg_recorded_displays.insert(name.clone(), entry);
                                                });
                                            }
                                            if let Some(remove) = to_remove {
                                                self.rg_recorded_displays.remove(&remove);
                                            }
                                            if self.rg_recorded_displays.is_empty() {
                                                ui.label("(还没有已记录的显示器。点击「添加当前」来添加。)");
                                            }
                                        });
                                    }
                                    _ => {}
                                }

                                // 保存 UI 状态
                                self.module_enabled.insert(name.to_string(), enabled);
                                self.module_auto_start.insert(name.to_string(), auto_start);
                            }
                        });
                    ui.add_space(4.0);
                }

                // ── 底部按钮 ──
                ui.add_space(8.0);
                ui.separator();
                ui.horizontal(|ui| {
                    if ui.button("保存").clicked() {
                        let new_config = self.build_output_config();
                        let _ = self.event_tx.send(SettingsEvent::Save(new_config));
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                    if ui.button("取消").clicked() {
                        let _ = self.event_tx.send(SettingsEvent::Cancel);
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                });
            });
        });
    }
}
