use crate::module::screen_guardian::ScreenGuardianPlugin;
use crate::module::refresh_guardian::RefreshGuardianPlugin;
use crate::module::{ModuleBase, ModuleContext, ModuleStatus};
use crate::config::manager::ConfigManager;
use flume::Sender;
use std::collections::HashMap;

/// 模块实例包装
struct ModuleEntry {
    instance: Box<dyn ModuleBase>,
    display_name: &'static str,
}

/// 模块生命周期管理器
pub struct ModuleManager {
    modules: HashMap<String, ModuleEntry>,
    notify_tx: Sender<(String, String)>,
}

impl ModuleManager {
    pub fn new(notify_tx: Sender<(String, String)>) -> Self {
        ModuleManager {
            modules: HashMap::new(),
            notify_tx,
        }
    }

    /// 启动所有 auto_start=true 的模块
    pub fn start_all_auto(&mut self, config_mgr: &ConfigManager) {
        let candidates: [(&str, Box<dyn ModuleBase>); 2] = [
            ("screen_guardian", Box::new(ScreenGuardianPlugin::new())),
            ("refresh_guardian", Box::new(RefreshGuardianPlugin::new())),
        ];

        for (name, mut instance) in candidates {
            let info = config_mgr.get_module_info(name);
            if !info.auto_start {
                log::info!("模块 {} auto_start=false，跳过", instance.display_name());
                continue;
            }
            if let Some(ref _cfg) = info.config.as_object() {
                if let Err(e) = instance.on_config_changed(&info.config) {
                    log::error!("模块 {} 配置热重载失败: {}", instance.display_name(), e);
                }
            }
            let display_name = instance.display_name();
            let ctx = ModuleContext {
                notify_tx: self.notify_tx.clone(),
            };
            match instance.start(ctx) {
                Ok(()) => {
                    log::info!("模块已启动: {}", display_name);
                    self.modules.insert(name.to_string(), ModuleEntry {
                        instance,
                        display_name: display_name,
                    });
                }
                Err(e) => {
                    log::error!("模块启动失败 {}: {}", name, e);
                }
            }
        }
    }

    /// 启动指定模块
    pub fn start_module(&mut self, name: &str, config_mgr: &ConfigManager) {
        if self.modules.contains_key(name) {
            log::warn!("模块 {} 已在运行", name);
            return;
        }
        let instance: Option<Box<dyn ModuleBase>> = match name {
            "screen_guardian" => Some(Box::new(ScreenGuardianPlugin::new())),
            "refresh_guardian" => Some(Box::new(RefreshGuardianPlugin::new())),
            _ => None,
        };
        let mut instance = match instance {
            Some(i) => i,
            None => {
                log::error!("未知模块: {}", name);
                return;
            }
        };

        let info = config_mgr.get_module_info(name);
        if let Err(e) = instance.on_config_changed(&info.config) {
            log::error!("模块 {} 配置热重载失败: {}", instance.display_name(), e);
        }

        let ctx = ModuleContext {
            notify_tx: self.notify_tx.clone(),
        };
        let display_name = instance.display_name();
        match instance.start(ctx) {
            Ok(()) => {
                log::info!("模块已启动: {}", display_name);
                self.modules.insert(name.to_string(), ModuleEntry {
                    instance,
                    display_name,
                });
            }
            Err(e) => {
                log::error!("模块启动失败 {}: {}", name, e);
            }
        }
    }

    /// 停止指定模块
    pub fn stop_module(&mut self, name: &str) -> bool {
        if let Some(mut entry) = self.modules.remove(name) {
            match entry.instance.stop(5) {
                Ok(true) => {
                    log::info!("模块已停止: {}", entry.display_name);
                    true
                }
                Ok(false) => {
                    log::warn!("模块停止超时: {}", entry.display_name);
                    // 超时后线程仍然是 detached，资源最终被 OS 回收
                    false
                }
                Err(e) => {
                    log::error!("停止模块 {} 失败: {}", name, e);
                    false
                }
            }
        } else {
            true
        }
    }

    /// 停止所有模块
    pub fn stop_all(&mut self) {
        let names: Vec<String> = self.modules.keys().cloned().collect();
        for name in names {
            self.stop_module(&name);
        }
    }

    /// 获取所有模块的状态（包括未运行的模块）
    pub fn get_all_status(&self) -> HashMap<String, ModuleStatus> {
        let mut statuses = HashMap::new();
        // 添加运行中的模块
        for (name, entry) in &self.modules {
            statuses.insert(name.clone(), entry.instance.get_status());
        }
        // 添加已注册但未运行的模块（状态为 Stopped），保证托盘始终显示全部模块
        for name in &["screen_guardian", "refresh_guardian"] {
            if !statuses.contains_key(*name) {
                statuses.insert(name.to_string(), ModuleStatus::stopped());
            }
        }
        statuses
    }

    /// 配置热重载
    pub fn on_config_changed(&mut self, module_name: &str, config: &serde_json::Value) {
        if let Some(entry) = self.modules.get_mut(module_name) {
            if let Err(e) = entry.instance.on_config_changed(config) {
                log::error!("模块 {} 配置热重载失败: {}", module_name, e);
            }
        }
    }

    /// 模块是否在运行
    pub fn is_running(&self, name: &str) -> bool {
        self.modules.contains_key(name)
    }
}
