//! ScreenTimeoutGuardian — 纯监控逻辑（无 UI、无配置文件 I/O）
//!
//! Ported from `modules/screen_guardian/core.py`.

use std::collections::HashMap;

use chrono::{DateTime, Local};
use serde_json::Value;

use crate::module::{ModuleState, ModuleStatus};
use crate::win32::power::PowerManager;

// ── 设定键名对照 ──────────────────────────────────────────
const SETTINGS: &[(&str, &str, &str, u32)] = &[
    // (internal_key, config_key, display_name, default_minutes)
    ("ac_display", "ac_display_timeout_minutes", "AC 熄屏", 10),
    ("dc_display", "dc_display_timeout_minutes", "DC 熄屏", 5),
    ("ac_sleep", "ac_sleep_timeout_minutes", "AC 睡眠", 30),
    ("dc_sleep", "dc_sleep_timeout_minutes", "DC 睡眠", 15),
];

const SECONDS_PER_MINUTE: u32 = 60;

/// 屏幕超时守护核心逻辑
///
/// 执行一次检查 + 修正，不管理线程和 UI。
pub struct ScreenGuardianCore<PM: PowerManager> {
    pm: PM,
    config: Value,
    on_correction: Option<Box<dyn Fn(&[String]) + Send + Sync>>,

    /// 上次检查的原始值（秒）
    pub last_values: HashMap<String, u32>,
    /// 上次检查的期望值（秒）
    pub last_expected: HashMap<String, u32>,
    /// 上次修正列表
    pub last_corrections: Vec<String>,
    /// 上次检查时间
    pub last_check_time: Option<DateTime<Local>>,
}

impl<PM: PowerManager> ScreenGuardianCore<PM> {
    pub fn new(config: Value, pm: PM) -> Self {
        ScreenGuardianCore {
            pm,
            config,
            on_correction: None,
            last_values: HashMap::new(),
            last_expected: HashMap::new(),
            last_corrections: Vec::new(),
            last_check_time: None,
        }
    }

    /// 热更新配置（线程安全由调用方保证）
    pub fn update_config(&mut self, config: Value) {
        self.config = config;
    }

    /// 设置修正回调
    pub fn set_on_correction(&mut self, cb: Box<dyn Fn(&[String]) + Send + Sync>) {
        self.on_correction = Some(cb);
    }

    /// 执行一次检查并修正，返回状态
    pub fn run_check(&mut self) -> ModuleStatus {
        let result = self._do_check();
        match result {
            Ok(status) => status,
            Err(e) => {
                log::error!("检查失败: {}", e);
                ModuleStatus::error(e.to_string())
            }
        }
    }

    // ── 内部实现 ──────────────────────────────────────

    fn _do_check(&mut self) -> Result<ModuleStatus, Box<dyn std::error::Error>> {
        let config = self.config.clone(); // 快照

        let scheme = self.pm.get_active_scheme()?;

        // 读取当前值
        let mut values: HashMap<String, u32> = HashMap::new();
        let mut expected: HashMap<String, u32> = HashMap::new();

        for &(key, cfg_key, _, default_mins) in SETTINGS {
            let v = self._read_timeout(&scheme, key)?;
            values.insert(key.to_string(), v);

            let cfg_mins = config
                .get(cfg_key)
                .and_then(|v| v.as_u64())
                .unwrap_or(default_mins as u64) as u32;
            expected.insert(key.to_string(), cfg_mins * SECONDS_PER_MINUTE);
        }

        // 比对并修正
        let mut corrections: Vec<String> = Vec::new();
        for &(key, _, name, _) in SETTINGS {
            let v = values[key];
            let e = expected[key];
            if v != e {
                log::warn!(
                    "{} 被修改: 当前 {}s ≠ 期望 {}s，准备修正",
                    name,
                    v,
                    e
                );
                self._write_timeout(&scheme, key, e)?;
                corrections.push(name.to_string());
            }
        }

        // 保存上次结果
        self.last_values = values;
        self.last_expected = expected;
        self.last_corrections = corrections.clone();
        self.last_check_time = Some(Local::now());

        if !corrections.is_empty() {
            // 套用变更
            self.pm.apply_scheme(&scheme)?;
            let msg = format!("修正完成: {}", corrections.join(", "));
            log::warn!("{}", msg);

            if config
                .get("show_notifications")
                .and_then(|v| v.as_bool())
                .unwrap_or(true)
            {
                if let Some(ref cb) = self.on_correction {
                    cb(&corrections);
                }
            }

            return Ok(ModuleStatus {
                state: ModuleState::Running,
                detail: format!("已修正: {}", corrections.join(", ")),
                last_check: self.last_check_time,
                was_corrected: true,
            });
        }

        Ok(ModuleStatus {
            state: ModuleState::Running,
            detail: "正常".to_string(),
            last_check: self.last_check_time,
            was_corrected: false,
        })
    }

    /// 根据内部键名派发读取调用
    fn _read_timeout(&self, scheme: &windows::core::GUID, key: &str) -> Result<u32, crate::win32::power::PowerError> {
        match key {
            "ac_display" => self.pm.read_ac_display_timeout(scheme),
            "dc_display" => self.pm.read_dc_display_timeout(scheme),
            "ac_sleep" => self.pm.read_ac_sleep_timeout(scheme),
            "dc_sleep" => self.pm.read_dc_sleep_timeout(scheme),
            _ => unreachable!("unknown setting key: {}", key),
        }
    }

    /// 根据内部键名派发写入调用
    fn _write_timeout(
        &self,
        scheme: &windows::core::GUID,
        key: &str,
        seconds: u32,
    ) -> Result<(), crate::win32::power::PowerError> {
        match key {
            "ac_display" => self.pm.write_ac_display_timeout(scheme, seconds),
            "dc_display" => self.pm.write_dc_display_timeout(scheme, seconds),
            "ac_sleep" => self.pm.write_ac_sleep_timeout(scheme, seconds),
            "dc_sleep" => self.pm.write_dc_sleep_timeout(scheme, seconds),
            _ => unreachable!("unknown setting key: {}", key),
        }
    }

    // ── 辅助方法 ──────────────────────────────────────

    /// 秒数 → 可读分钟
    fn _format_minutes(&self, seconds: u32) -> String {
        if seconds == 0 {
            return "永不".to_string();
        }
        format!("{}分", seconds / SECONDS_PER_MINUTE)
    }

    /// 生成托盘 tooltip 文本行（供独立模式使用）
    pub fn get_tooltip_lines(&self) -> Vec<String> {
        let mut lines = vec!["ScreenTimeoutGuardian — 监控中".to_string()];
        if let Some(t) = self.last_check_time {
            lines.push(format!("检查: {}", t.format("%H:%M:%S")));
        }
        for &(key, _, name, _) in SETTINGS {
            let v = self.last_values.get(key).copied().unwrap_or(0);
            let e = self.last_expected.get(key).copied().unwrap_or(0);
            lines.push(format!(
                "{}: {} (期望 {})",
                name,
                self._format_minutes(v),
                self._format_minutes(e)
            ));
        }
        if !self.last_corrections.is_empty() {
            lines.push(format!("⚠ 刚刚已修正: {}", self.last_corrections.join(", ")));
        }
        lines
    }
}

// ── Tests ─────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;
    use crate::win32::power::MockPowerManager;
    use std::collections::HashMap;

    fn make_config() -> Value {
        serde_json::json!({
            "ac_display_timeout_minutes": 10,
            "dc_display_timeout_minutes": 5,
            "ac_sleep_timeout_minutes": 30,
            "dc_sleep_timeout_minutes": 15,
            "show_notifications": true,
        })
    }

    #[test]
    fn test_new_defaults() {
        let config = make_config();
        let pm = MockPowerManager::new();
        let core = ScreenGuardianCore::new(config, pm);
        assert!(core.last_values.is_empty());
        assert!(core.last_corrections.is_empty());
        assert!(core.last_check_time.is_none());
    }

    #[test]
    fn test_run_check_no_correction_needed() {
        // Mock starts with the same values the config expects (in seconds)
        // ac_display: 10 * 60 = 600, dc_display: 5 * 60 = 300
        // ac_sleep: 30 * 60 = 1800, dc_sleep: 15 * 60 = 900
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 600);
        map.insert("dc_display".into(), 300);
        map.insert("ac_sleep".into(), 1800);
        map.insert("dc_sleep".into(), 900);
        let pm = MockPowerManager::from_map(map);
        let config = make_config();
        let mut core = ScreenGuardianCore::new(config, pm);

        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "正常");
        assert!(!status.was_corrected);
        assert!(core.last_check_time.is_some());
        assert!(core.last_corrections.is_empty());
    }

    #[test]
    fn test_run_check_correction_needed() {
        // Mock starts with wrong values
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 999);
        map.insert("dc_display".into(), 999);
        map.insert("ac_sleep".into(), 999);
        map.insert("dc_sleep".into(), 999);
        let pm = MockPowerManager::from_map(map);
        let config = make_config();
        let mut core = ScreenGuardianCore::new(config, pm);

        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert!(status.detail.starts_with("已修正"));
        assert!(status.was_corrected);
        assert_eq!(core.last_corrections.len(), 4);

        // last_values 是修正前的快照，原始值是 999
        assert_eq!(core.last_values["ac_display"], 999);
    }

    #[test]
    fn test_run_check_partial_correction() {
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 600); // already correct
        map.insert("dc_display".into(), 999); // wrong
        map.insert("ac_sleep".into(), 1800);  // already correct
        map.insert("dc_sleep".into(), 999);   // wrong
        let pm = MockPowerManager::from_map(map);
        let config = make_config();
        let mut core = ScreenGuardianCore::new(config, pm);

        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert!(status.was_corrected);
        assert_eq!(core.last_corrections.len(), 2);
        assert!(core.last_corrections.contains(&"DC 熄屏".to_string()));
        assert!(core.last_corrections.contains(&"DC 睡眠".to_string()));
    }

    #[test]
    fn test_run_check_callback_fired() {
        let pm = MockPowerManager::new();
        let config = make_config();
        let mut core = ScreenGuardianCore::new(config, pm);

        let fired = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let fired_clone = fired.clone();
        core.set_on_correction(Box::new(move |_corrections| {
            fired_clone.store(true, std::sync::atomic::Ordering::SeqCst);
        }));

        let _status = core.run_check();
        assert!(fired.load(std::sync::atomic::Ordering::SeqCst));
    }

    #[test]
    fn test_get_tooltip_lines_after_check() {
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 600);
        map.insert("dc_display".into(), 300);
        map.insert("ac_sleep".into(), 1800);
        map.insert("dc_sleep".into(), 900);
        let pm = MockPowerManager::from_map(map);
        let config = make_config();
        let mut core = ScreenGuardianCore::new(config, pm);

        core.run_check();
        let lines = core.get_tooltip_lines();
        assert!(lines[0].contains("监控中"));
        assert!(lines.iter().any(|l| l.contains("AC 熄屏")));
        assert!(lines.iter().any(|l| l.contains("DC 熄屏")));
        // No corrections, so no warning line
        assert!(!lines.iter().any(|l| l.contains("修正")));
    }

    #[test]
    fn test_update_config() {
        let pm = MockPowerManager::new();
        let mut core = ScreenGuardianCore::new(make_config(), pm);

        let new_config = serde_json::json!({
            "ac_display_timeout_minutes": 1,
            "dc_display_timeout_minutes": 1,
            "ac_sleep_timeout_minutes": 1,
            "dc_sleep_timeout_minutes": 1,
            "show_notifications": false,
        });
        core.update_config(new_config);

        let status = core.run_check();
        assert!(status.was_corrected);
        // last_values 是修正前的快照（MockPowerManager 默认值）
        assert_eq!(core.last_values["ac_display"], 300);
        // 验证修正列表有 4 项
        assert_eq!(core.last_corrections.len(), 4);
    }
}
