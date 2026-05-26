pub mod core;

use crate::module::{ModuleBase, ModuleContext, ModuleStatus, ModuleState};
use crate::module::screen_guardian::core::ScreenGuardianCore;
use crate::win32::power::WindowsPowerManager;
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use std::thread::{self, JoinHandle};

pub struct ScreenGuardianPlugin {
    name: &'static str,
    display_name: &'static str,
    description: &'static str,
    /// Arc<Mutex> 在线程间共享
    core: Arc<Mutex<Option<ScreenGuardianCore<WindowsPowerManager>>>>,
    thread: Option<JoinHandle<()>>,
    running: Arc<AtomicBool>,
    restart_signal: Arc<AtomicBool>,
    config: Arc<Mutex<serde_json::Value>>,
    last_status: Arc<Mutex<ModuleStatus>>,
}

impl ScreenGuardianPlugin {
    pub fn new() -> Self {
        ScreenGuardianPlugin {
            name: "screen_guardian",
            display_name: "屏幕超时守护",
            description: "保持显示器超时和睡眠超时在期望值",
            core: Arc::new(Mutex::new(None)),
            thread: None,
            running: Arc::new(AtomicBool::new(false)),
            restart_signal: Arc::new(AtomicBool::new(false)),
            config: Arc::new(Mutex::new(serde_json::Value::Object(serde_json::Map::new()))),
            last_status: Arc::new(Mutex::new(ModuleStatus::running())),
        }
    }
}

impl ModuleBase for ScreenGuardianPlugin {
    fn name(&self) -> &'static str { self.name }
    fn display_name(&self) -> &'static str { self.display_name }
    fn description(&self) -> &'static str { self.description }

    fn start(&mut self, ctx: ModuleContext) -> Result<(), Box<dyn std::error::Error>> {
        let config = self.config.lock().unwrap().clone();

        // P0-1: 从配置读取检查间隔（默认 30 秒，最小 5 秒）
        let check_interval = config.get("check_interval_seconds")
            .and_then(|v| v.as_u64())
            .unwrap_or(30)
            .max(5) as usize;

        let mut core = ScreenGuardianCore::new(config, WindowsPowerManager);

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
        let core_arc = self.core.clone();
        let restart = self.restart_signal.clone();
        let last_status_arc = self.last_status.clone();

        self.thread = Some(thread::spawn(move || {
            while running.load(Ordering::SeqCst) {
                let status = {
                    let mut guard = core_arc.lock().unwrap();
                    guard.as_mut().map(|c| c.run_check())
                };

                // P0-2: 缓存最后一次 run_check() 结果
                if let Some(ref s) = status {
                    *last_status_arc.lock().unwrap() = s.clone();
                }

                match status {
                    Some(ModuleStatus { state: ModuleState::Error, detail, .. }) => {
                        log::error!("屏幕超时守护检查失败: {}", detail);
                    }
                    _ => {}
                }
                // 分段 sleep，可被 restart_signal 打断
                for _ in 0..check_interval {
                    if !running.load(Ordering::SeqCst) { break; }
                    if restart.swap(false, Ordering::SeqCst) { break; }
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
                    self.core.lock().unwrap().take();
                    return Ok(true);
                }
                if start.elapsed() >= timeout {
                    return Ok(false);
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        }
        self.core.lock().unwrap().take();
        Ok(true)
    }

    fn get_status(&self) -> ModuleStatus {
        // P0-2: 返回缓存的最后一次 run_check() 结果（含真实 detail）
        self.last_status.lock().unwrap().clone()
    }

    fn on_config_changed(&mut self, config: &serde_json::Value) -> Result<(), Box<dyn std::error::Error>> {
        *self.config.lock().unwrap() = config.clone();
        let mut guard = self.core.lock().unwrap();
        if let Some(ref mut core) = *guard {
            core.update_config(config.clone());
        }
        self.restart_signal.store(true, Ordering::SeqCst);
        Ok(())
    }
}
