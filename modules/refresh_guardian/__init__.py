"""
RefreshRateGuardian — 显示器刷新率守护模块插件

功能:
  - 检测已连接显示器的当前刷新率
  - 对比记录的期望刷新率，偏离时自动修正
  - 提供双列表 UI：左侧已连接 / 右侧已记录
"""

import logging
import threading
import time
from typing import Optional

from module_base import ModuleBase, ModuleStatus
from modules.refresh_guardian.core import RefreshRateGuardianCore

MIN_CHECK_INTERVAL = 5

# ── 导入 tkinter 时避免模块级崩溃 ────────────────────────
try:
    from tkinter import ttk
    _TKINTER_AVAILABLE = True
except ImportError:
    _TKINTER_AVAILABLE = False


class ModulePlugin(ModuleBase):
    """显示器刷新率守护模块插件"""

    name = "refresh_guardian"
    display_name = "刷新率守护"
    description = "保持各显示器运行在期望的刷新率"

    def __init__(self) -> None:
        super().__init__()
        self._core: RefreshRateGuardianCore | None = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._config: dict = {}
        self._check_interval = 60

    # ── 生命周期 ──────────────────────────────────────────

    def _start(self) -> None:
        """启动监控线程"""
        self._core = RefreshRateGuardianCore(
            config=self._config,
            on_correction=self._on_correction,
        )
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logging.info('刷新率守护模块已启动')

    def _stop(self, timeout: float = 5.0) -> bool:
        """停止监控线程"""
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
        return self._thread is not None and self._thread.is_alive()

    # ── 配置 ──────────────────────────────────────────────

    def on_config_changed(self, config: dict) -> None:
        """热重载配置"""
        self._config = dict(config)
        if self._core:
            self._core.update_config(self._config)
        logging.info('刷新率守护配置已热重载')

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
            interval = self._get_check_interval()
            self._check_interval = interval
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
            assert self._core is not None
            status = self._core.run_check()
            self._status = status
        finally:
            self._lock.release()

    def _get_check_interval(self) -> int:
        return max(self._config.get('check_interval_seconds', 60), MIN_CHECK_INTERVAL)

    # ── 通知回调 ──────────────────────────────────────────

    def _on_correction(self, corrections: list[str]) -> None:
        """修正发生时的回调"""
        if not corrections:
            return
        msg = f'已自动修正: {", ".join(corrections)}'
        logging.info(msg)
        if self._notify_callback and self._config.get('show_notifications', True):
            self._notify_callback(self.display_name, msg)

    # ── 配置 UI ──────────────────────────────────────────

    def build_config_frame(self, parent) -> tuple:
        """构建模块配置 UI：双列表（已连接 / 已记录）"""
        if not _TKINTER_AVAILABLE:
            import tkinter as _tk
            frame = _tk.Frame(parent)
            _tk.Label(frame, text='tkinter 不可用').pack()
            return frame, lambda i18n: None

        from tkinter import ttk, StringVar, BooleanVar, messagebox
        import tkinter as tk
        from utils.i18n import _

        frame = ttk.Frame(parent)

        # 配置项容器（用于 get_config_from_ui）
        self._config_widgets: dict = {}
        self._display_rows: list[dict] = []  # 已记录显示器的 UI 行信息
        self._connected_vars: dict[str, tk.Label] = {}  # 已连接 -> 标签

        # ── 控制行：检查间隔 + 刷新按钮 ──
        ctrl_row = ttk.Frame(frame)
        ctrl_row.pack(fill='x', pady=(0, 6))

        self._interval_lbl = ttk.Label(ctrl_row, text=_('refresh_guardian.check_interval') + ':')
        self._interval_lbl.pack(side='left')
        self._interval_var = tk.IntVar(value=self._config.get('check_interval_seconds', 60))
        interval_spin = ttk.Spinbox(ctrl_row, from_=5, to=600,
                                    textvariable=self._interval_var, width=5)
        interval_spin.pack(side='left', padx=(4, 2))
        self._seconds_lbl = ttk.Label(ctrl_row, text=_('unit.seconds'))
        self._seconds_lbl.pack(side='left')

        # 通知开关
        self._notify_var = tk.BooleanVar(value=self._config.get('show_notifications', True))
        self._notify_cb = ttk.Checkbutton(ctrl_row, text=_('refresh_guardian.show_notifications'),
                                          variable=self._notify_var)
        self._notify_cb.pack(side='right', padx=(6, 0))

        # ── 双列表 ──
        lists_row = ttk.Frame(frame)
        lists_row.pack(fill='both', expand=True, pady=(4, 0))

        # 左侧：已连接显示器
        left_frame = ttk.LabelFrame(lists_row, text=_('refresh_guardian.connected'),
                                    padding=4)
        left_frame.pack(side='left', fill='both', expand=True, padx=(0, 4))

        self._connected_list = ttk.Treeview(
            left_frame, columns=('rate',), height=6,
        )
        self._connected_list.heading('#0', text=_('refresh_guardian.display_name'))
        self._connected_list.column('#0', width=180)
        self._connected_list.heading('rate', text='Hz')
        self._connected_list.column('rate', width=60, anchor='center')
        self._connected_list.pack(fill='both', expand=True)

        refresh_btn = ttk.Button(
            left_frame, text=_('refresh_guardian.refresh'),
            command=self._refresh_connected_list,
        )
        refresh_btn.pack(fill='x', pady=(2, 0))
        self._refresh_connected_list()

        # 右侧：已记录显示器
        right_frame = ttk.LabelFrame(lists_row, text=_('refresh_guardian.recorded'),
                                     padding=4)
        right_frame.pack(side='right', fill='both', expand=True, padx=(4, 0))

        # 使用 Canvas + Scrollbar 实现可滚动
        right_canvas = tk.Canvas(right_frame, highlightthickness=0)
        right_scrollbar = ttk.Scrollbar(right_frame, orient='vertical',
                                        command=right_canvas.yview)
        recorded_inner = ttk.Frame(right_canvas)

        recorded_inner.bind('<Configure>',
                            lambda e: right_canvas.configure(scrollregion=right_canvas.bbox('all')))
        right_canvas.create_window((0, 0), window=recorded_inner, anchor='nw')
        right_canvas.configure(yscrollcommand=right_scrollbar.set)

        right_canvas.pack(side='left', fill='both', expand=True)
        right_scrollbar.pack(side='right', fill='y')

        self._recorded_frame = recorded_inner
        self._recorded_rows: list[dict] = []  # 记录每行的 widget 信息

        # 底部操作按钮
        action_row = ttk.Frame(right_frame)
        action_row.pack(fill='x', pady=(2, 0))

        add_btn = ttk.Button(
            action_row, text=_('refresh_guardian.add_current'),
            command=self._add_current_displays,
        )
        add_btn.pack(side='left', padx=(0, 4))

        clear_btn = ttk.Button(
            action_row, text=_('refresh_guardian.clear_all'),
            command=self._clear_recorded,
        )
        clear_btn.pack(side='left')

        # 重建已记录列表
        self._rebuild_recorded_list()

        # ── 语言刷新回调 ──
        def on_lang_change(i18n):
            left_frame.configure(text=_('refresh_guardian.connected'))
            right_frame.configure(text=_('refresh_guardian.recorded'))
            refresh_btn.configure(text=_('refresh_guardian.refresh'))
            add_btn.configure(text=_('refresh_guardian.add_current'))
            clear_btn.configure(text=_('refresh_guardian.clear_all'))
            self._interval_lbl.config(text=_('refresh_guardian.check_interval') + ':')
            self._seconds_lbl.config(text=_('unit.seconds'))
            self._notify_cb.config(text=_('refresh_guardian.show_notifications'))

        return frame, on_lang_change

    # ── UI 辅助方法 ───────────────────────────────────────

    def _refresh_connected_list(self) -> None:
        """刷新左侧已连接显示器列表"""
        from utils.i18n import _
        from utils.display_manager import get_connected_displays

        # 清空
        for item_id in self._connected_list.get_children():
            self._connected_list.delete(item_id)

        displays = get_connected_displays()
        for d in displays:
            label = f'{d["friendly_name"]} ({d["name"]})'
            self._connected_list.insert('', 'end', text=label,
                                        values=(f'{d["current_refresh_rate"]}Hz',))

    def _rebuild_recorded_list(self) -> None:
        """重建右侧已记录显示器列表"""
        from utils.i18n import _
        # 销毁 _recorded_frame 的全部子控件（包括未被追踪的"暂无记录" label）
        for w in self._recorded_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._recorded_rows.clear()

        displays_config: dict = self._config.get('displays', {})
        if not displays_config:
            ttk.Label(self._recorded_frame,
                      text=_('refresh_guardian.no_recorded')).pack()
            return

        for name, entry in displays_config.items():
            self._add_recorded_row(name, entry)

    def _add_recorded_row(self, name: str, entry: dict) -> None:
        """添加一行已记录显示器 UI"""
        from utils.display_manager import get_current_settings
        from tkinter import ttk, BooleanVar, StringVar
        from utils.i18n import _

        row_frame = ttk.Frame(self._recorded_frame)
        row_frame.pack(fill='x', pady=2)

        # 启用开关
        enabled_var = BooleanVar(value=entry.get('enabled', True))

        # 显示器名称
        settings = get_current_settings(name)
        display_label = name
        if settings:
            display_label = f'{name} ({settings["width"]}×{settings["height"]})'

        cb = ttk.Checkbutton(row_frame, text=display_label, variable=enabled_var)
        cb.pack(side='left')

        # 刷新率下拉
        rate_var = StringVar(value=str(entry.get('expected_refresh_rate', 60)))
        rate_combo = ttk.Combobox(
            row_frame, textvariable=rate_var, state='readonly', width=8,
        )

        # 获取支持的刷新率
        if settings:
            try:
                from utils.display_manager import get_supported_refresh_rates
                supported = get_supported_refresh_rates(
                    name, settings['width'], settings['height'],
                )
            except Exception:
                supported = [60, 75, 90, 100, 120, 144, 165, 180, 200, 240]
        else:
            supported = [60, 75, 90, 100, 120, 144, 165, 180, 200, 240]

        rate_combo['values'] = [f'{r}Hz' for r in supported]
        rate_combo.pack(side='left', padx=(4, 0))

        # 删除按钮
        def make_del(n=name):
            return lambda: self._delete_recorded(n)

        del_btn = ttk.Button(row_frame, text='✕', width=2,
                             command=make_del())
        del_btn.pack(side='left', padx=(4, 0))

        self._recorded_rows.append({
            'name': name,
            'frame': row_frame,
            'enabled_var': enabled_var,
            'rate_var': rate_var,
            'cb': cb,
            'combo': rate_combo,
            'del_btn': del_btn,
        })

    def _delete_recorded(self, name: str) -> None:
        """删除已记录的显示器"""
        # 拷贝后再修改，避免影响监控线程持有的 config 引用
        displays_config = dict(self._config.get('displays', {}))
        displays_config.pop(name, None)
        new_config = dict(self._config)
        new_config['displays'] = displays_config
        self._config = new_config
        self._rebuild_recorded_list()

    def _add_current_displays(self) -> None:
        """将当前已连接的显示器添加到记录列表"""
        from utils.i18n import _
        from utils.display_manager import get_connected_displays

        displays_config = dict(self._config.get('displays', {}))
        connected = get_connected_displays()

        added = 0
        for d in connected:
            name = d['name']
            if name not in displays_config:
                displays_config[name] = {
                    'enabled': True,
                    'expected_refresh_rate': d['current_refresh_rate'],
                }
                added += 1

        new_config = dict(self._config)
        new_config['displays'] = displays_config
        self._config = new_config
        self._rebuild_recorded_list()

        if added > 0:
            messagebox.showinfo(
                _('config.title'),
                _('refresh_guardian.added_count', count=added),
            )

    def _clear_recorded(self) -> None:
        """清空所有已记录显示器"""
        from tkinter import messagebox
        from utils.i18n import _

        if not messagebox.askyesno(_('config.title'),
                                    _('refresh_guardian.confirm_clear')):
            return

        new_config = dict(self._config)
        new_config['displays'] = {}
        self._config = new_config
        self._rebuild_recorded_list()

    # ── 读取 UI 配置 ──────────────────────────────────────

    def get_config_from_ui(self) -> dict:
        """从 UI 控件获取当前配置值"""
        result: dict = {}
        result['check_interval_seconds'] = self._interval_var.get()
        result['show_notifications'] = bool(self._notify_var.get())

        displays: dict = {}
        for row in self._recorded_rows:
            name = row['name']
            enabled = row['enabled_var'].get()
            rate_str = row['rate_var'].get().replace('Hz', '').strip()
            try:
                rate = int(rate_str) if rate_str else 60
            except ValueError:
                rate = 60
            displays[name] = {
                'enabled': bool(enabled),
                'expected_refresh_rate': rate,
            }
        result['displays'] = displays

        return result
