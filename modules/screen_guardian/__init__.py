"""
ScreenTimeoutGuardian — 模块插件封装
"""

import logging
import threading
import time
from typing import Optional

from module_base import ModuleBase, ModuleStatus
from modules.screen_guardian.core import ScreenGuardianCore, CONFIG_KEYS, SECONDS_PER_MINUTE

MIN_CHECK_INTERVAL = 5  # 最小检查间隔（秒）

# ── 导入 tkinter 时避免模块级崩溃 ────────────────────────
try:
    from tkinter import ttk, IntVar, BooleanVar
    _TKINTER_AVAILABLE = True
except ImportError:
    _TKINTER_AVAILABLE = False


class ModulePlugin(ModuleBase):
    """屏幕超时守护模块插件"""

    name = "screen_guardian"
    display_name = "屏幕超时守护"
    description = "定时检查 AC/DC 熄屏与睡眠超时，被篡改时自动修正"

    def __init__(self) -> None:
        super().__init__()
        self._core: ScreenGuardianCore | None = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._config: dict = {}

    # ── 生命周期 ──────────────────────────────────────────

    def _start(self) -> None:
        """启动监控线程"""
        self._core = ScreenGuardianCore(
            config=self._config,
            on_correction=self._on_correction,
        )
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logging.info('屏幕超时守护模块已启动')

    def _stop(self, timeout: float = 5.0) -> bool:
        """停止监控线程（self._running 由父类 start() 管理）"""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    # ── 状态 ──────────────────────────────────────────────

    def get_status(self) -> ModuleStatus:
        """获取模块状态"""
        if not self._core:
            return ModuleStatus(state='stopped', detail='未初始化')

        if self._status.state == 'running' and not self._alive():
            return ModuleStatus(state='error', detail='监控线程已意外终止')

        if not self._running and self._alive():
            return ModuleStatus(state='error', detail='停止线程超时')

        return ModuleStatus(
            state=self._status.state,
            detail=self._status.detail,
            last_check=self._core.last_check_time,
        )

    def _alive(self) -> bool:
        """检查线程是否存活"""
        return self._thread is not None and self._thread.is_alive()

    # ── 配置 ──────────────────────────────────────────────

    def on_config_changed(self, config: dict) -> None:
        """热重载配置"""
        self._config = dict(config)
        if self._core:
            self._core.update_config(self._config)
        logging.info('屏幕超时守护配置已热重载')

    def set_initial_config(self, config: dict) -> None:
        """启动前设置初始配置（Manager 模式）"""
        self._config = dict(config)

    # ── 监控循环 ──────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """后台监控主循环"""
        while self._running:
            try:
                self._heartbeat = time.monotonic()
                self._do_check()
            except Exception as e:
                logging.error(f'检查过程发生例外: {e}')
                self._status = ModuleStatus(state='error', detail=str(e))
            # 分段 sleep 以快速响应停止信号
            interval = self._get_check_interval()
            self._check_interval = interval  # 同步给 is_alive() 做动态阈值
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    def _do_check(self) -> None:
        """执行一次检查（线程安全）"""
        if not self._lock.acquire(blocking=False):
            logging.debug('上一次检查尚未完成，跳过本次')
            return
        try:
            assert self._core is not None  # _do_check 仅在 _start 后调用
            status = self._core.run_check()
            self._status = status
        finally:
            self._lock.release()

    def _get_check_interval(self) -> int:
        """获取安全的检查间隔"""
        return max(self._config.get('check_interval_seconds', 30), MIN_CHECK_INTERVAL)

    # ── 通知回调 ──────────────────────────────────────────

    def _on_correction(self, corrections: list[str]) -> None:
        """修正发生时的回调"""
        if not corrections:
            return
        msg = f'已自动修正: {", ".join(corrections)}'
        logging.info(msg)
        # 托盘通知（由 Manager 注入 _notify_callback）
        if self._notify_callback and self._config.get('show_notifications', True):
            self._notify_callback(self.display_name, msg)

    # ── 配置 UI ──────────────────────────────────────────

    def build_config_frame(self, parent) -> tuple:
        """构建模块配置 UI"""
        if not _TKINTER_AVAILABLE:
            # tkinter 不可用时的降级路径（理论上不会触发）
            import tkinter as _tk
            frame = _tk.Frame(parent)
            _tk.Label(frame, text='tkinter 不可用').pack()
            return frame, lambda i18n: None

        from tkinter import ttk, IntVar, BooleanVar
        frame = ttk.Frame(parent)
        from utils.i18n import _


        # 配置项容器
        self._config_widgets: dict[str, object] = {}
        rows = []

        def add_row(label_key: str, cfg_key: str, unit_key: str, default: int):
            """添加一行配置"""
            row = ttk.Frame(frame)
            row.pack(fill='x', pady=1)

            lbl = ttk.Label(row, text=_(label_key) + ':')
            lbl.pack(side='left')

            var = IntVar(value=self._config.get(cfg_key, default))
            spin = ttk.Spinbox(row, from_=0, to=999, textvariable=var, width=5)
            spin.pack(side='left', padx=(4, 0))

            unit_lbl = ttk.Label(row, text=_(unit_key))
            unit_lbl.pack(side='left', padx=(2, 0))

            self._config_widgets[cfg_key] = var
            rows.append((lbl, unit_lbl, label_key, unit_key))

        add_row('screen_guardian.check_interval', 'check_interval_seconds',
                'unit.seconds', 30)
        add_row('screen_guardian.ac_display', 'ac_display_timeout_minutes',
                'unit.minutes', 10)
        add_row('screen_guardian.dc_display', 'dc_display_timeout_minutes',
                'unit.minutes', 5)
        add_row('screen_guardian.ac_sleep', 'ac_sleep_timeout_minutes',
                'unit.minutes', 30)
        add_row('screen_guardian.dc_sleep', 'dc_sleep_timeout_minutes',
                'unit.minutes', 15)

        # 通知开关
        notify_row = ttk.Frame(frame)
        notify_row.pack(fill='x', pady=(4, 0))
        notify_var = BooleanVar(value=self._config.get('show_notifications', True))
        notify_cb = ttk.Checkbutton(notify_row, text=_('screen_guardian.show_notifications'),
                                    variable=notify_var)
        notify_cb.pack(anchor='w')
        self._config_widgets['show_notifications'] = notify_var

        # 语言刷新回调
        def on_lang_change(i18n):
            for lbl, unit_lbl, label_key, unit_key in rows:
                lbl.config(text=_(label_key) + ':')
                unit_lbl.config(text=_(unit_key))
            notify_cb.config(text=_('screen_guardian.show_notifications'))

        # 从 config_widgets 读取当前值的方法
        def get_config() -> dict:
            result = {}
            for key, widget in self._config_widgets.items():
                if isinstance(widget, BooleanVar):
                    result[key] = bool(widget.get())
                elif isinstance(widget, IntVar):
                    result[key] = int(widget.get())
            return result

        # 保存引用
        self._config_widgets['_get_fn'] = get_config

        return frame, on_lang_change

    def get_config_from_ui(self) -> dict:
        """从 UI 控件获取当前配置值"""
        get_fn = getattr(self, '_config_widgets', {}).get('_get_fn')
        if get_fn:
            return get_fn()
        return {}
