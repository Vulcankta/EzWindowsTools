use crate::config::schema::{try_migrate_from_legacy, AppConfig, ManagerConfig, ModuleInfo};
use crate::i18n::strip_jsonc;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

/// 集中式配置管理器（线程安全，原子写入）
pub struct ConfigManager {
    config: Arc<RwLock<AppConfig>>,
    path: PathBuf,
}

impl ConfigManager {
    // ── 加载 ────────────────────────────────────────────────

    /// 加载或创建默认配置
    pub fn load<P: AsRef<Path>>(path: P) -> Self {
        let path = path.as_ref().to_path_buf();
        let config = if path.exists() {
            Self::load_from_file(&path).unwrap_or_else(|e| {
                log::warn!("配置文件损坏，使用默认值: {}", e);
                AppConfig::default()
            })
        } else {
            let cfg = AppConfig::default();
            if let Err(e) = Self::write_atomic(&path, &cfg) {
                log::error!("创建默认配置文件失败: {}", e);
            }
            cfg
        };

        ConfigManager {
            config: Arc::new(RwLock::new(config)),
            path,
        }
    }

    /// 从磁盘读取并解析配置文件（含 JSONC 剥离和旧版迁移）
    fn load_from_file(path: &Path) -> Result<AppConfig, Box<dyn std::error::Error>> {
        let raw = std::fs::read_to_string(path)?;
        let cleaned = strip_jsonc(&raw);
        let value: serde_json::Value = serde_json::from_str(&cleaned)?;

        // 检测旧版并迁移
        if let Some(migrated) = try_migrate_from_legacy(&value) {
            log::info!("检测到旧版配置文件，迁移至新版格式");
            Self::write_atomic(path, &migrated)?;
            return Ok(migrated);
        }

        let config: AppConfig = serde_json::from_value(value)?;
        Ok(config)
    }

    // ── 原子写入 ────────────────────────────────────────────

    /// 原子写入：先写 `.json.tmp` 再 rename 替换（纯函数，不依赖 self）
    fn write_atomic(path: &Path, config: &AppConfig) -> Result<(), Box<dyn std::error::Error>> {
        let json = serde_json::to_string_pretty(config)?;
        let tmp_path = path.with_extension("json.tmp");
        std::fs::write(&tmp_path, &json)?;
        std::fs::rename(&tmp_path, path)?;
        Ok(())
    }

    /// 保存当前配置到文件（获取读锁）
    #[allow(dead_code)]
    fn save(&self) -> Result<(), Box<dyn std::error::Error>> {
        let config = self.config.read().map_err(|e| e.to_string())?;
        Self::write_atomic(&self.path, &config)
    }

    // ── 公开读取 ────────────────────────────────────────────

    /// 获取管理器配置（深拷贝等价，线程安全）
    pub fn get_manager_config(&self) -> ManagerConfig {
        self.config
            .read()
            .map(|c| c.manager.clone())
            .unwrap_or_default()
    }

    /// 获取模块完整 info（含 enabled / auto_start / config），缺失时返回默认值
    pub fn get_module_info(&self, name: &str) -> ModuleInfo {
        self.config
            .read()
            .ok()
            .and_then(|c| c.modules.get(name).cloned())
            .unwrap_or_else(|| ModuleInfo {
                enabled: true,
                auto_start: false,
                config: serde_json::Value::Object(serde_json::Map::new()),
            })
    }

    /// 模块是否启用（默认 true）
    pub fn is_module_enabled(&self, name: &str) -> bool {
        self.config
            .read()
            .ok()
            .and_then(|c| c.modules.get(name).map(|m| m.enabled))
            .unwrap_or(true)
    }

    /// 模块是否自动启动（默认 false）
    pub fn is_module_auto_start(&self, name: &str) -> bool {
        self.config
            .read()
            .ok()
            .and_then(|c| c.modules.get(name).map(|m| m.auto_start))
            .unwrap_or(false)
    }

    // ── 写入 ────────────────────────────────────────────────
    //
    // 注意：所有 setter 使用 Self::write_atomic(&self.path, &c) 而不是 self.save()
    //       以避免在持有写锁时尝试获取读锁导致的死锁（RwLock 不可重入）。

    /// 更新管理器配置
    pub fn set_manager_config(&self, config: ManagerConfig) {
        if let Ok(mut c) = self.config.write() {
            c.manager = config;
            if let Err(e) = Self::write_atomic(&self.path, &c) {
                log::error!("保存管理器配置失败: {}", e);
            }
        }
    }

    /// 设置模块启用状态（仅持久化，不触发模块级热重载）
    pub fn set_module_enabled(&self, name: &str, enabled: bool) {
        if let Ok(mut c) = self.config.write() {
            c.modules
                .entry(name.to_string())
                .or_insert_with(|| ModuleInfo {
                    enabled: true,
                    auto_start: false,
                    config: serde_json::Value::Object(serde_json::Map::new()),
                })
                .enabled = enabled;
            if let Err(e) = Self::write_atomic(&self.path, &c) {
                log::error!("保存模块启用状态失败: {}", e);
            }
        }
    }

    /// 设置模块自动启动状态（仅持久化，不触发热重载）
    pub fn set_module_auto_start(&self, name: &str, auto_start: bool) {
        if let Ok(mut c) = self.config.write() {
            c.modules
                .entry(name.to_string())
                .or_insert_with(|| ModuleInfo {
                    enabled: true,
                    auto_start: false,
                    config: serde_json::Value::Object(serde_json::Map::new()),
                })
                .auto_start = auto_start;
            if let Err(e) = Self::write_atomic(&self.path, &c) {
                log::error!("保存模块自动启动状态失败: {}", e);
            }
        }
    }

    /// 设置模块的 config 部分并触发热重载通知
    pub fn set_module_config(&self, name: &str, module_config: serde_json::Value) {
        if let Ok(mut c) = self.config.write() {
            c.modules
                .entry(name.to_string())
                .or_insert_with(|| ModuleInfo {
                    enabled: true,
                    auto_start: false,
                    config: serde_json::Value::Object(serde_json::Map::new()),
                })
                .config = module_config;
            if let Err(e) = Self::write_atomic(&self.path, &c) {
                log::error!("保存模块配置失败: {}", e);
            }
        }
    }

    // ── 重新加载 ────────────────────────────────────────────

    /// 获取完整配置的 JSON 值
    pub fn get_full_config_json(&self) -> serde_json::Value {
        self.config.read().map(|c| serde_json::to_value(&*c).unwrap_or_default()).unwrap_or_default()
    }

    /// 应用完整的配置（来自设置窗口）
    pub fn apply_full_config(&self, new_config: serde_json::Value) {
        if let Ok(mut c) = self.config.write() {
            if let Ok(parsed) = serde_json::from_value::<AppConfig>(new_config) {
                *c = parsed;
                if let Err(e) = Self::write_atomic(&self.path, &c) {
                    log::error!("保存配置失败: {}", e);
                }
            }
        }
    }

    /// 从磁盘重新加载配置
    pub fn reload(&self) {
        if let Ok(mut c) = self.config.write() {
            match Self::load_from_file(&self.path) {
                Ok(new_config) => {
                    *c = new_config;
                }
                Err(e) => {
                    log::warn!("重新加载配置失败，保留现有配置: {}", e);
                }
            }
        }
    }
}
