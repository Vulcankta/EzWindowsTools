pub mod core;

use crate::module::{ModuleBase, ModuleContext, ModuleStatus, ModuleState};
use crate::module::refresh_guardian::core::RefreshRateGuardianCore;
use crate::win32::display::WindowsDisplayManager;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::thread::{self, JoinHandle};

pub struct RefreshGuardianPlugin {
    name: &'static str,
    display_name: &'static str,
    description: &'static str,
    core: Arc<Mutex<Option<RefreshRateGuardianCore<WindowsDisplayManager>>>>,
    thread: Option<JoinHandle<()>>,
    running: Arc<AtomicBool>,
    restart_signal: Arc<AtomicBool>,
    config: Arc<Mutex<serde_json::Value>>,
    last_status: Arc<Mutex<ModuleStatus>>,
}

impl RefreshGuardianPlugin {
    pub fn new() -> Self {
        RefreshGuardianPlugin {
            name: "refresh_guardian",
            display_name: "刷新率守护",
            description: "保持各显示器运行在期望的刷新率",
            core: Arc::new(Mutex::new(None)),
            thread: None,
            running: Arc::new(AtomicBool::new(false)),
            restart_signal: Arc::new(AtomicBool::new(false)),
            config: Arc::new(Mutex::new(serde_json::Value::Object(serde_json::Map::new()))),
            last_status: Arc::new(Mutex::new(ModuleStatus::running())),
        }
    }
}

impl ModuleBase for RefreshGuardianPlugin {
    fn name(&self) -> &'static str {
        self.name
    }

    fn display_name(&self) -> &'static str {
        self.display_name
    }

    fn description(&self) -> &'static str {
        self.description
    }

    fn start(&mut self, ctx: ModuleContext) -> Result<(), Box<dyn std::error::Error>> {
        let config = self.config.lock().unwrap().clone();
        let check_interval = config
            .get("check_interval_seconds")
            .and_then(|v| v.as_u64())
            .unwrap_or(60)
            .max(5) as usize;

        let mut core = RefreshRateGuardianCore::new(config, WindowsDisplayManager);

        // 设置通知回调
        let tx = ctx.notify_tx.clone();
        let display_name = self.display_name;
        core.set_on_correction(Box::new(move |corrections| {
            let msg = format!("已修正: {}", corrections.join(", "));
            log::info!("{}: {}", display_name, msg);
            let _ = tx.send((display_name.to_string(), msg));
        }));

        *self.core.lock().unwrap() = Some(core);
        self.running.store(true, Ordering::SeqCst);

        let running = self.running.clone();
        let core_clone = self.core.clone();
        let restart = self.restart_signal.clone();
        let last_status_arc = self.last_status.clone();

        self.thread = Some(thread::spawn(move || {
            while running.load(Ordering::SeqCst) {
                let status = {
                    let mut guard = core_clone.lock().unwrap();
                    if let Some(ref mut core_ref) = *guard {
                        core_ref.run_check()
                    } else {
                        break;
                    }
                };
                *last_status_arc.lock().unwrap() = status.clone();
                if status.state == ModuleState::Error {
                    log::error!("刷新率守护检查失败: {}", status.detail);
                }
                // 分段 sleep，可被 restart_signal 打断
                for _ in 0..check_interval {
                    if !running.load(Ordering::SeqCst) {
                        break;
                    }
                    if restart.swap(false, Ordering::SeqCst) {
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_secs(1));
                }
            }
        }));

        Ok(())
    }

    fn stop(&mut self, timeout_secs: u64) -> Result<bool, Box<dyn std::error::Error>> {
        self.running.store(false, Ordering::SeqCst);
        if let Some(handle) = self.thread.take() {
            let start = std::time::Instant::now();
            let timeout = std::time::Duration::from_secs(timeout_secs);
            loop {
                if handle.is_finished() {
                    return Ok(true);
                }
                if start.elapsed() >= timeout {
                    return Ok(false);
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        }
        Ok(true)
    }

    fn get_status(&self) -> ModuleStatus {
        self.last_status.lock().unwrap().clone()
    }

    fn on_config_changed(
        &mut self,
        config: &serde_json::Value,
    ) -> Result<(), Box<dyn std::error::Error>> {
        *self.config.lock().unwrap() = config.clone();
        let mut guard = self.core.lock().unwrap();
        if let Some(ref mut core) = *guard {
            core.update_config(config.clone());
        }
        self.restart_signal.store(true, Ordering::SeqCst);
        Ok(())
    }
}
