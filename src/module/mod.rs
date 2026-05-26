pub mod refresh_guardian;
pub mod screen_guardian;
pub mod manager;

use chrono::{DateTime, Local};

/// 模块运行状态
#[derive(Debug, Clone, PartialEq)]
pub enum ModuleState {
    Running,
    Stopped,
    Error,
}

/// 模块状态快照
#[derive(Debug, Clone)]
pub struct ModuleStatus {
    pub state: ModuleState,
    pub detail: String,
    pub last_check: Option<DateTime<Local>>,
    pub was_corrected: bool,
}

impl ModuleStatus {
    pub fn running() -> Self {
        ModuleStatus {
            state: ModuleState::Running,
            detail: String::new(),
            last_check: None,
            was_corrected: false,
        }
    }

    pub fn stopped() -> Self {
        ModuleStatus {
            state: ModuleState::Stopped,
            detail: "已停止".into(),
            last_check: None,
            was_corrected: false,
        }
    }

    pub fn error(msg: impl Into<String>) -> Self {
        ModuleStatus {
            state: ModuleState::Error,
            detail: msg.into(),
            last_check: None,
            was_corrected: false,
        }
    }
}

/// 模块上下文（线程安全通道）
#[derive(Clone)]
pub struct ModuleContext {
    /// 通知通道：发送 (title, message) 给管理器
    pub notify_tx: flume::Sender<(String, String)>,
}

/// 模块基类 trait
pub trait ModuleBase: Send {
    fn name(&self) -> &'static str;
    fn display_name(&self) -> &'static str;
    fn description(&self) -> &'static str;

    /// 启动模块（会启动后台监控线程）
    fn start(&mut self, ctx: ModuleContext) -> Result<(), Box<dyn std::error::Error>>;

    /// 停止模块，timeout 为等待线程退出的最大秒数
    fn stop(&mut self, timeout_secs: u64) -> Result<bool, Box<dyn std::error::Error>>;

    /// 获取当前状态快照
    fn get_status(&self) -> ModuleStatus;

    /// 配置热重载
    fn on_config_changed(&mut self, config: &serde_json::Value) -> Result<(), Box<dyn std::error::Error>>;
}

/// 统一模块枚举（编译期已知集合）
#[derive(Debug)]
pub enum AllModules {
    ScreenGuardian,
    RefreshGuardian,
}

impl AllModules {
    pub fn all() -> &'static [AllModules] {
        &[AllModules::ScreenGuardian, AllModules::RefreshGuardian]
    }

    pub fn name(&self) -> &'static str {
        match self {
            AllModules::ScreenGuardian => "screen_guardian",
            AllModules::RefreshGuardian => "refresh_guardian",
        }
    }
}
