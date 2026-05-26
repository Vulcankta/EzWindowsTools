use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// 管理器全局配置
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagerConfig {
    #[serde(default)]
    pub auto_start: bool,
    #[serde(default = "default_language")]
    pub language: String,
}

fn default_language() -> String {
    "zh-CN".into()
}

impl Default for ManagerConfig {
    fn default() -> Self {
        ManagerConfig {
            auto_start: false,
            language: "zh-CN".into(),
        }
    }
}

/// 单个模块的配置信息（包含启用/自动启动/自定义配置）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleInfo {
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub auto_start: bool,
    #[serde(default)]
    pub config: serde_json::Value,
}

fn default_enabled() -> bool {
    true
}

impl Default for ModuleInfo {
    fn default() -> Self {
        ModuleInfo {
            enabled: true,
            auto_start: false,
            config: serde_json::Value::Object(serde_json::Map::new()),
        }
    }
}

/// 顶层应用配置（新版集中式格式）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default)]
    pub manager: ManagerConfig,
    #[serde(default)]
    pub modules: HashMap<String, ModuleInfo>,
}

impl Default for AppConfig {
    fn default() -> Self {
        let mut modules = HashMap::new();

        // ── screen_guardian ──
        let mut sg_config = serde_json::Map::new();
        sg_config.insert("check_interval_seconds".into(), 30.into());
        sg_config.insert("ac_display_timeout_minutes".into(), 10.into());
        sg_config.insert("dc_display_timeout_minutes".into(), 5.into());
        sg_config.insert("ac_sleep_timeout_minutes".into(), 30.into());
        sg_config.insert("dc_sleep_timeout_minutes".into(), 15.into());
        sg_config.insert("show_notifications".into(), true.into());

        modules.insert(
            "screen_guardian".into(),
            ModuleInfo {
                enabled: true,
                auto_start: true,
                config: serde_json::Value::Object(sg_config),
            },
        );

        // ── refresh_guardian ──
        let mut rg_config = serde_json::Map::new();
        rg_config.insert("check_interval_seconds".into(), 60.into());
        rg_config.insert("show_notifications".into(), true.into());
        rg_config.insert(
            "displays".into(),
            serde_json::Value::Object(serde_json::Map::new()),
        );

        modules.insert(
            "refresh_guardian".into(),
            ModuleInfo {
                enabled: true,
                auto_start: true,
                config: serde_json::Value::Object(rg_config),
            },
        );

        AppConfig {
            manager: ManagerConfig {
                auto_start: false,
                language: "zh-CN".into(),
            },
            modules,
        }
    }
}

/// 旧版扁平配置（用于迁移检测与解析）
#[derive(Deserialize)]
struct LegacyConfig {
    #[serde(rename = "check_interval_seconds")]
    check_interval: Option<u32>,
    #[serde(rename = "ac_display_timeout_minutes")]
    ac_display: Option<u32>,
    #[serde(rename = "dc_display_timeout_minutes")]
    dc_display: Option<u32>,
    #[serde(rename = "ac_sleep_timeout_minutes")]
    ac_sleep: Option<u32>,
    #[serde(rename = "dc_sleep_timeout_minutes")]
    dc_sleep: Option<u32>,
    #[serde(rename = "show_notifications")]
    show_notifications: Option<bool>,
}

/// 尝试将旧版扁平配置迁移到新版结构。
///
/// 检测条件：顶层存在旧版 key（如 `ac_display_timeout_minutes`）
/// 且不存在新版 `manager` key。
pub fn try_migrate_from_legacy(value: &serde_json::Value) -> Option<AppConfig> {
    if value.get("ac_display_timeout_minutes").is_some() && value.get("manager").is_none() {
        if let Ok(old) = serde_json::from_value::<LegacyConfig>(value.clone()) {
            let mut config = AppConfig::default();
            if let Some(v) = old.check_interval {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["check_interval_seconds"] = serde_json::json!(v);
                }
            }
            if let Some(v) = old.ac_display {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["ac_display_timeout_minutes"] = serde_json::json!(v);
                }
            }
            if let Some(v) = old.dc_display {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["dc_display_timeout_minutes"] = serde_json::json!(v);
                }
            }
            if let Some(v) = old.ac_sleep {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["ac_sleep_timeout_minutes"] = serde_json::json!(v);
                }
            }
            if let Some(v) = old.dc_sleep {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["dc_sleep_timeout_minutes"] = serde_json::json!(v);
                }
            }
            if let Some(v) = old.show_notifications {
                if let Some(m) = config.modules.get_mut("screen_guardian") {
                    m.config["show_notifications"] = serde_json::json!(v);
                }
            }
            return Some(config);
        }
    }
    None
}
