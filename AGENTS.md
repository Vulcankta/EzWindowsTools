# EzWindowsTools — Agent Guide

## Project Overview

Windows 电源管理模块化管理平台。将单体 ScreenTimeoutGuardian 重构为插件式架构，支持多模块管理、国际化 GUI 配置、开机自启等。

## Architecture (3 Phases)

### Phase 0 — 模块化重构
```
module_base.py          # ModuleStatus dataclass + ModuleBase ABC
modules/__init__.py     # 模块发现引擎（正常: pkgutil, 冻结: 目录扫描）
modules/screen_guardian/
  ├── __init__.py       # ModulePlugin（继承 ModuleBase）
  ├── core.py           # 纯监控逻辑（无 UI/线程/配置文件 I/O）
  └── power_manager.py  # Windows powrprof.dll API（共享）
guardian.py             # 向后兼容独立入口（引用模块结构）
```

### Phase 1 — 管理框架
```
config_manager.py       # 线程安全集中配置（Lock + 原子写入 + 快照回调）
module_manager.py       # 模块生命周期（发现/启停/精确热重载）
manager.py              # 管理器主入口
ui/tray_manager.py      # 多模块状态聚合托盘（4色图标）
utils/auto_start.py     # 注册表自启动 + bat 备选
```

### Phase 2 — GUI + 国际化
```
utils/i18n.py           # I18n 引擎 + 全局 _() 函数
locales/en.jsonc        # 英文基准（32 keys）
locales/zh-CN.jsonc     # 简体中文
locales/zh-TW.jsonc     # 繁体中文
ui/config_window.py     # tkinter 设置窗口（语言/自启/模块配置）
```

## Key Design Decisions

### Thread Model
- **主线程**: tkinter mainloop（`root.after` 轮询 + 事件处理）
- **后台线程**: pystray 托盘（daemon=True）
- **跨线程通信**: `queue.Queue` + `root.after(200ms, poll)`
- **模块线程**: 各模块自行管理 daemon 线程

### 配置变更通知链
```
config_window._on_save()
  → set_module_config(name, config_dict)
    → _notify(name) → _on_module_config_changed(name, config)
      → instance.on_config_changed(config['config'])
  → set_module_enabled/auto_start（仅持久化，不触发通知）
  → start_module/stop_module（由调用方管理）
  → refresh_tray()
```

### i18n Fallback Chain
```
_(key) → I18n.get(key):
  1. self._current.get(key)     # 当前语言
  2. or self._fallback.get(key)  # en 基准
  3. or key                       # 原文
```

### 通知链
```
ScreenGuardianCore.run_check() → 修正
  → _on_correction(corrections)
    → self._notify_callback(title, msg)    # ModuleBase 属性
      → Manager._module_notify()
        → tray._icon.notify()              # Windows 通知
        → root.after(0, refresh_tray)      # 图标变红
  → _auto_refresh_tray (每10s)             # 状态正常→变绿
```

### 安全退出
```
quit():
  1. stop_all(3.0) — 停止所有模块
  2. tray.stop() — 停止托盘
  3. tray_thread.join(2.0) — 等待托盘线程（避免 self-join）
  4. root.quit() — 退出 tkinter mainloop
```

## Agent Guidelines

### Adding a New Module
1. Create `modules/xxx/__init__.py` with `class ModulePlugin(ModuleBase)`
2. Override: `name`, `display_name`, `description`, `_start()`, `_stop()`, `get_status()`, `on_config_changed()`
3. Optionally implement `build_config_frame()` and `get_config_from_ui()`
4. Import `_(key)` for any user-visible strings
5. Module is auto-discovered by `modules/__init__.py`

### Adding a New Language
1. Create `locales/xx.jsonc` with `$code` and `$name` metadata
2. Fill in keys; missing keys fall back to English
3. File is auto-detected by `I18n._scan()`

### Common Pitfalls
- **pystray MenuItem callbacks receive `icon` as first arg** → wrap with `lambda *args, n=name: handler(n)`
- **`logging.info()` triggers `basicConfig()`** → set up handlers before any log call
- **`root.after_idle` / `after(0)` not thread-safe on some Tcl builds** → use `queue.Queue` + `root.after` polling
- **`transient(parent)` with withdrawn parent** → some Windows versions hide the child window
- **PyInstaller frozen paths** → use `sys._MEIPASS` for data files, `sys.executable.parent` for config/logs

### File Dependency Map
```
manager.py
  ├── config_manager.py   → utils/jsonc.py
  ├── module_manager.py   → module_base.py, modules/, config_manager.py
  ├── modules/            → module_base.py
  ├── ui/tray_manager.py  → utils/i18n.py
  ├── ui/config_window.py → utils/i18n.py, modules/
  └── utils/i18n.py       → utils/jsonc.py
```

### Test Build
```powershell
python -m PyInstaller --onedir --windowed --name EzWindowsTools `
  --add-data "locales;locales" --add-data "modules;modules" `
  --hidden-import power_manager --hidden-import PIL --hidden-import pystray `
  --collect-submodules modules manager.py
```
