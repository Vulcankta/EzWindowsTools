# EzWindowsTools — Agent Guide (Rust)

## Project Overview

Windows 电源管理模块化托盘守护程序。Rust 重构版，替换了原 Python (~2674 行) 的 PyInstaller+tkinter+pystray 技术栈。

**关键指标**: 20 源文件, ~2100 行 Rust, 30 单元测试, Release 3.18 MB。

## Architecture (5 Phases)

### Phase 0 — 项目骨架 + Win32 API
```
src/
├── main.rs                # 入口（日志/配置/模块/托盘/主循环）
├── lib.rs                 # 库入口
├── win32/
│   ├── mod.rs
│   ├── power.rs           # PowerManager trait + WindowsPowerManager + MockPowerManager
│   └── display.rs         # DisplayManager trait + WindowsDisplayManager + MockDisplayManager
├── config/
│   ├── mod.rs
│   ├── schema.rs          # AppConfig / ModuleInfo / LegacyConfig（serde）
│   └── manager.rs         # ConfigManager（RwLock + 原子写入 .tmp→rename）
└── i18n.rs                # I18n 引擎 + JSONC 注释剥离
```

### Phase 1 — 模块框架
```
src/module/
├── mod.rs                 # ModuleBase trait + ModuleStatus + ModuleContext + AllModules
├── manager.rs             # ModuleManager（启停 + 热重载 + 状态聚合）
├── screen_guardian/
│   ├── mod.rs             # ScreenGuardianPlugin（线程封装 + restart_signal）
│   └── core.rs            # 纯监控逻辑（泛型 PowerManager）
└── refresh_guardian/
    ├── mod.rs             # RefreshGuardianPlugin（线程封装 + restart_signal）
    └── core.rs            # 纯监控逻辑（泛型 DisplayManager）
```

### Phase 2 — 系统托盘
```
src/tray.rs                # TrayManager（4色图标生成 + 动态菜单 + 独立线程）
```

### Phase 3 — GUI 设置窗口
```
src/gui/mod.rs             # egui 设置窗口（语言 + 模块折叠 + 刷新率双列表）
```

### Phase 4 — 发布
```
src/auto_start.rs          # winreg HKCU\Run 自启动
Cargo.toml                 # Release profile（opt-level=z + lto=fat + strip）
.github/workflows/build.yml  # GitHub Actions CI
locales/                   # en / zh-CN / zh-TW.jsonc
```

## Key Design Decisions

### Thread Model
```
main thread (主循环 200ms 轮询)
  ├─ flume::notify_rx ←── ScreenGuardianPlugin (daemon)
  │                     └── RefreshGuardianPlugin (daemon)
  ├─ flume::tray_event ←── tray thread (daemon, Shell_NotifyIconW)
  └─ flume::settings_rx ←── eframe settings window (独立线程)
```

模块线程使用 `Arc<AtomicBool>` 控制运行/停止，`restart_signal` 打断 sleep 循环。所有线程均为 daemon 模式。

### 配置变更通知链
```
main.rs SettingsEvent::Save
  → config_mgr.apply_full_config(new_config)
    → RwLock write + 原子写入磁盘
  → module_mgr.on_config_changed("screen_guardian", &cfg)
    → ModulePlugin.on_config_changed(cfg)
      → core.update_config(cfg)
      → restart_signal.set()  // 打断当前 sleep
  → tray.update_status()
```

### i18n Fallback Chain
```
i18n.get(key) → &str:
  1. self.current.get(key)      # 当前语言
  2. or self.fallback.get(key)  # en 基准
  3. or key                      # 原文
```

### 通知链
```
Core.run_check() → 修正
  → on_correction callback
    → notify_tx.send((title, msg))
      → main loop 接收
        → tray.notify()            # Shell_NotifyIconW (NIM_ADD→NIM_DELETE)
        → tray.update_status()     # 图标变色
  → 主循环每 10s 定时刷新托盘
```

### 安全退出
```
Ctrl+C / 托盘「退出」:
  1. running.store(false) → 主循环退出
  2. module_mgr.stop_all()
     → 各模块 Plugin.stop()
       → running=false
       → handle.poll(50ms, timeout=5s)
  3. tray.stop()
     → cmd_tx.send(Quit) + handle.join()
```

## Module System

### ModuleBase Trait
```rust
pub trait ModuleBase: Send {
    fn name(&self) -> &'static str;
    fn display_name(&self) -> &'static str;
    fn description(&self) -> &'static str;
    fn start(&mut self, ctx: ModuleContext) -> Result<(), Box<dyn Error>>;
    fn stop(&mut self, timeout_secs: u64) -> Result<bool, Box<dyn Error>>;
    fn get_status(&self) -> ModuleStatus;
    fn on_config_changed(&mut self, config: &Value) -> Result<(), Box<dyn Error>>;
}
```

### Core/Plugin 分离
- **core.rs**: 纯监控逻辑，泛型 trait（PowerManager / DisplayManager），可单元测试
- **mod.rs**: Plugin 结构体，封装 Arc<Mutex<Option<Core>>> + JoinHandle + AtomicBool

### 线程模式
```rust
Plugin.start():
  1. 从 config 读取 check_interval 等参数
  2. 创建 Core 并设置 on_correction callback
  3. 启动 thread::spawn 循环
  4. 循环：lock core → run_check → unlock → sleep(interval)

Plugin.stop():
  running=false → wait handle.is_finished(timeout)
```

## Agent Guidelines

### Adding a New Module
1. Create `src/module/xxx/core.rs` with generic core logic
2. Create `src/module/xxx/mod.rs` with Plugin struct implementing ModuleBase
3. Add `pub mod xxx;` to `src/module/mod.rs`
4. Add default config to `src/config/schema.rs` `AppConfig::default()`
5. Register in `src/module/manager.rs` start_all_auto / start_module
6. Add GUI config UI in `src/gui/mod.rs`

### Adding a New Language
1. Create `locales/xx.jsonc` with standard keys
2. Add `"locale.name": "Language Name"` metadata key
3. File is auto-detected by I18n::load() on next startup

### Common Pitfalls
- **windows-rs GUID**: Use `GUID::from_u128(0x...)` not `GUID::from_utf8` (doesn't exist)
- **Power API**: AC 函数返回 `WIN32_ERROR` struct, DC 函数返回 `u32` — 需用 `WIN32_ERROR(err)` 包装
- **HKEY 参数**: Power API 用 `HKEY::default()` 而非 `None::<HKEY>`（HKEY 是 InterfaceType）
- **tray-icon 菜单**: `MenuEvent::receiver()` 返回全局单例，`PredefinedMenuItem` 在 muda 0.15 不存在
- **eframe 隐藏窗口**: 不用 withdraw/hide，直接按需 `run_native` 创建窗口
- **std::path::absolute**: Rust 1.95 中不稳定，用 `Path::new("locales")` 相对路径
- **flume vs crossbeam**: 项目用 flume, API 更轻量

### Build Commands
```powershell
# 开发构建
cargo build

# 运行
cargo run

# 测试
cargo test

# Release（优化体积）
cargo build --release

# 构建产物
target/release/ezwindows-tools.exe  # 3.18 MB, standalone
```

### File Dependency Map
```
src/main.rs
  ├── config/manager.rs   → config/schema.rs, i18n.rs
  ├── module/manager.rs   → module/mod.rs, module/*/mod.rs
  │   ├── screen_guardian/ → screen_guardian/core.rs
  │   └── refresh_guardian/ → refresh_guardian/core.rs
  ├── tray.rs             → module/mod.rs
  ├── gui/mod.rs          → config/schema.rs, i18n.rs, win32/display.rs
  ├── auto_start.rs       → winreg
  └── i18n.rs             → (JSONC 文件)
```
