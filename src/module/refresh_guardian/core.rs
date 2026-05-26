//! RefreshRateGuardian — 纯监控逻辑（无 UI、无配置文件 I/O）
//!
//! Ported from Python `modules/refresh_guardian/core.py`.
//! No threads, no config file I/O — all side effects are delegated through
//! the [`DisplayManager`] trait and an optional callback.

use chrono::{DateTime, Local};
use serde_json::Value;
use std::collections::HashMap;

use crate::module::{ModuleState, ModuleStatus};
use crate::win32::display::DisplayManager;

/// 显示器刷新率守护核心逻辑
///
/// 不管理线程，不处理配置文件 I/O。所有副作用通过回调函数和
/// [`DisplayManager`] trait 委派。
pub struct RefreshRateGuardianCore<DM: DisplayManager> {
    dm: DM,
    config: Value,
    on_correction: Option<Box<dyn Fn(&[String]) + Send + Sync>>,
    pub last_values: HashMap<String, u32>,
    pub last_check_time: Option<DateTime<Local>>,
    pub last_corrections: Vec<String>,
}

impl<DM: DisplayManager> RefreshRateGuardianCore<DM> {
    /// 创建一个新的 RefreshRateGuardianCore 实例。
    ///
    /// * `config` — 初始配置（serde_json::Value 格式）
    /// * `dm`     — 实现了 [`DisplayManager`] trait 的实例
    pub fn new(config: Value, dm: DM) -> Self {
        Self {
            dm,
            config,
            on_correction: None,
            last_values: HashMap::new(),
            last_check_time: None,
            last_corrections: Vec::new(),
        }
    }

    /// 热重载配置
    pub fn update_config(&mut self, config: Value) {
        self.config = config;
    }

    /// 设置修正回调（当检测到显示器刷新率偏离目标值时调用）
    pub fn set_on_correction(&mut self, cb: Box<dyn Fn(&[String]) + Send + Sync>) {
        self.on_correction = Some(cb);
    }

    /// 执行一次检查，发现偏离时自动修正。
    ///
    /// 返回 [`ModuleStatus`]：
    /// - `Running`: 检查正常或已修正
    /// - `Error`:   检查过程发生异常
    pub fn run_check(&mut self) -> ModuleStatus {
        match self._do_check() {
            Ok(status) => status,
            Err(e) => {
                log::error!("检查过程发生异常: {}", e);
                ModuleStatus::error(e.to_string())
            }
        }
    }

    /// 内部检查逻辑（返回 `Result` 以便统一错误处理）
    fn _do_check(&mut self) -> Result<ModuleStatus, Box<dyn std::error::Error>> {
        // 1. 快照配置（clone）
        let config = self.config.clone();

        // 2. 获取 displays 配置: {display_name: {expected_refresh_rate, enabled}}
        let displays_config = config["displays"]
            .as_object()
            .cloned()
            .unwrap_or_default();

        // 3. 获取已连接显示器的当前刷新率
        let connected = self.dm.get_connected_displays()?;

        // 4. 如果没有连接显示器 → 返回 running
        if connected.is_empty() {
            self.last_check_time = Some(Local::now());
            return Ok(ModuleStatus {
                state: ModuleState::Running,
                detail: "未检测到显示器".to_string(),
                last_check: self.last_check_time,
                was_corrected: false,
            });
        }

        // 5. 保存当前值到快照
        self.last_values = HashMap::new();
        for display in &connected {
            self.last_values
                .insert(display.name.clone(), display.current_refresh_rate);
        }

        let show_notifications = config["show_notifications"].as_bool().unwrap_or(true);
        let mut corrections: Vec<String> = Vec::new();

        // 6. 检查每个已连接的显示器
        for display in &connected {
            let name = &display.name;
            let friendly = &display.friendly_name;
            let current_hz = display.current_refresh_rate;

            // 6a. 在配置中查找此显示器
            let entry = match displays_config.get(name) {
                Some(v) => v,
                None => continue, // 未记录此显示器
            };

            let expected_hz = entry["expected_refresh_rate"].as_u64().unwrap_or(0) as u32;
            let entry_enabled = entry["enabled"].as_bool().unwrap_or(true);

            // 6b. 跳过已禁用或期望值不合法的显示器
            if !entry_enabled || expected_hz == 0 {
                continue;
            }

            // 6c. 1Hz 容差（NTSC vs 整数精度问题，如 59.94→59 或 119.88→119）
            let diff = if current_hz > expected_hz {
                current_hz - expected_hz
            } else {
                expected_hz - current_hz
            };
            if diff <= 1 {
                continue;
            }

            // 6d. 尝试修正
            let (success, msg) = self.dm.set_refresh_rate(name, expected_hz)?;
            if success {
                corrections.push(format!("{} {}→{}Hz", friendly, current_hz, expected_hz));
            } else {
                corrections.push(format!("{} 修正失败: {}", friendly, msg));
            }
        }

        // 7. 更新快照
        self.last_corrections = corrections.clone();
        self.last_check_time = Some(Local::now());

        // 8. 如果有修正发生
        if !corrections.is_empty() {
            let msg = corrections.join("\n");
            log::warn!("{}", msg);

            if show_notifications {
                if let Some(ref cb) = self.on_correction {
                    cb(&corrections);
                }
            }

            let state = if corrections.iter().any(|c| c.contains("失败")) {
                ModuleState::Error
            } else {
                ModuleState::Running
            };

            return Ok(ModuleStatus {
                state,
                detail: msg,
                last_check: self.last_check_time,
                was_corrected: true,
            });
        }

        // 9. 无修正
        Ok(ModuleStatus {
            state: ModuleState::Running,
            detail: "检查正常".to_string(),
            last_check: self.last_check_time,
            was_corrected: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::win32::display::MockDisplayManager;
    use serde_json::json;

    /// 辅助函数：创建带有一台显示器的 MockDisplayManager
    fn mock_dm_one_display(name: &str, friendly: &str, hz: u32) -> MockDisplayManager {
        MockDisplayManager {
            displays: vec![crate::win32::display::DisplayInfo {
                name: name.to_string(),
                friendly_name: friendly.to_string(),
                current_refresh_rate: hz,
                current_width: 1920,
                current_height: 1080,
            }],
            supported_rates: vec![hz, 144],
        }
    }

    #[test]
    fn test_no_displays() {
        let dm = MockDisplayManager {
            displays: vec![],
            supported_rates: vec![],
        };
        let config = json!({});
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "未检测到显示器");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_no_config_entry() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({}); // no "displays" key
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_already_correct() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 60,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_ntsc_tolerance() {
        // 59.94 → 60: within 1Hz tolerance, should be skipped
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 59);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 60,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_ntsc_tolerance_119_120() {
        // 119.88 → 120: within 1Hz tolerance
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 119);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 120,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_successful_correction() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert!(status.was_corrected);
        assert!(status.detail.contains("Mock Monitor"));
        assert!(status.detail.contains("60→144Hz"));
    }

    #[test]
    fn test_disabled_entry_skipped() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": false
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_zero_expected_skipped() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 0,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert_eq!(status.state, ModuleState::Running);
        assert_eq!(status.detail, "检查正常");
        assert!(!status.was_corrected);
    }

    #[test]
    fn test_on_correction_callback() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);

        let called = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let called_clone = called.clone();
        core.set_on_correction(Box::new(move |corrections| {
            assert!(!corrections.is_empty());
            assert!(corrections[0].contains("60→144Hz"));
            called_clone.store(true, std::sync::atomic::Ordering::SeqCst);
        }));

        let status = core.run_check();
        assert!(status.was_corrected);
        assert!(called.load(std::sync::atomic::Ordering::SeqCst));
    }

    #[test]
    fn test_update_config() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 60,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);

        // Initially no correction needed
        let status = core.run_check();
        assert!(!status.was_corrected);

        // Hot-reload config to expect 144Hz
        core.update_config(json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": true
                }
            }
        }));

        let status = core.run_check();
        assert!(status.was_corrected);
        assert!(status.detail.contains("60→144Hz"));
    }

    #[test]
    fn test_last_values_snapshot() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({});
        let mut core = RefreshRateGuardianCore::new(config, dm);
        core.run_check();

        assert_eq!(core.last_values.len(), 1);
        assert_eq!(
            *core.last_values.get(r"\\.\DISPLAY1").unwrap(),
            60
        );
    }

    #[test]
    fn test_last_corrections_updated() {
        let dm = mock_dm_one_display(r"\\.\DISPLAY1", "Mock Monitor", 60);
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        core.run_check();

        assert_eq!(core.last_corrections.len(), 1);
        assert!(core.last_corrections[0].contains("60→144Hz"));
    }

    #[test]
    fn test_multiple_displays_some_unconfigured() {
        let dm = MockDisplayManager {
            displays: vec![
                crate::win32::display::DisplayInfo {
                    name: r"\\.\DISPLAY1".to_string(),
                    friendly_name: "Monitor A".to_string(),
                    current_refresh_rate: 60,
                    current_width: 1920,
                    current_height: 1080,
                },
                crate::win32::display::DisplayInfo {
                    name: r"\\.\DISPLAY2".to_string(),
                    friendly_name: "Monitor B".to_string(),
                    current_refresh_rate: 60,
                    current_width: 1920,
                    current_height: 1080,
                },
            ],
            supported_rates: vec![60, 144],
        };
        // Only DISPLAY1 is configured
        let config = json!({
            "displays": {
                r"\\.\DISPLAY1": {
                    "expected_refresh_rate": 144,
                    "enabled": true
                }
            }
        });
        let mut core = RefreshRateGuardianCore::new(config, dm);
        let status = core.run_check();
        assert!(status.was_corrected);
        assert!(status.detail.contains("60→144Hz"));
        // DISPLAY2 was unconfigured and should not appear in corrections
        assert!(!status.detail.contains("Monitor B"));
    }
}
