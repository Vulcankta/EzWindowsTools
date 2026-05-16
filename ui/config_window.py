"""
EzWindowsTools 设置窗口 — tkinter GUI
"""

import logging
import threading
from pathlib import Path
from tkinter import ttk, Toplevel, StringVar, IntVar, BooleanVar, filedialog, messagebox
from typing import TYPE_CHECKING, Optional

from utils.i18n import _

if TYPE_CHECKING:
    from manager import Manager


class ConfigWindow:
    """管理器设置窗口"""

    def __init__(self, manager: 'Manager') -> None:
        self._manager = manager
        self._i18n = manager.i18n
        self._window: Optional[Toplevel] = None
        self._module_frames: dict[str, tuple] = {}  # name -> (frame, on_lang_change_cb)
        self._status_labels: dict[str, ttk.Label] = {}

    def show(self) -> None:
        """显示/激活设置窗口"""
        try:
            if self._window is not None and self._window.winfo_exists():
                self._window.lift()
                self._window.focus_force()
                return

            self._build_window()
            if self._window:
                self._window.protocol('WM_DELETE_WINDOW', self._on_close)
        except Exception as e:
            logging.error(f'显示设置窗口失败: {e}', exc_info=True)

    def _build_window(self) -> None:
        """构建窗口 UI"""
        self._window = Toplevel(self._manager.root)
        self._window.title(_('config.title'))
        self._window.geometry('640x560')
        self._window.minsize(520, 400)
        # 窗口居中
        self._window.update_idletasks()
        w = self._window.winfo_width()
        h = self._window.winfo_height()
        sw = self._window.winfo_screenwidth()
        sh = self._window.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self._window.geometry(f'{w}x{h}+{x}+{y}')
        # 不设置 transient：父窗口 root 是 withdraw 隐藏状态
        # 在某些 Windows 版本上 transient 到隐藏窗口会导致子窗口也不显示
        # self._window.transient(self._manager.root)

        # ── 主框架 ──
        main = ttk.Frame(self._window, padding=12)
        main.pack(fill='both', expand=True)

        # ── 语言选择（顶部） ──
        lang_frame = ttk.Frame(main)
        lang_frame.pack(fill='x', pady=(0, 8))
        ttk.Label(lang_frame, text=_('config.language') + ':').pack(side='left')
        self._lang_var = StringVar(value=self._i18n.current_language)
        self._lang_combo = ttk.Combobox(
            lang_frame, textvariable=self._lang_var, state='readonly', width=30,
        )
        self._lang_combo.pack(side='left', padx=(6, 0))
        self._lang_combo.bind('<<ComboboxSelected>>', self._on_language_selected)
        self._refresh_lang_list()
        ttk.Button(lang_frame, text=_('config.language.browse'),
                   command=self._browse_lang_file).pack(side='left', padx=(6, 0))

        # ── 全局设置 ──
        self._auto_start_var = BooleanVar(
            value=self._manager.config_mgr.get_manager_config().get('auto_start', False),
        )
        global_frame = ttk.LabelFrame(main, text=_('config.auto_start'), padding=6)
        global_frame.pack(fill='x', pady=(0, 8))
        cb = ttk.Checkbutton(global_frame, text=_('config.auto_start'),
                             variable=self._auto_start_var)
        cb.pack(anchor='w')

        # ── 模块列表（可折叠 + 可滚动） ──
        scroll_outer = ttk.Frame(main)
        scroll_outer.pack(fill='both', expand=True)

        # Canvas + Scrollbar 实现可滚动区域
        import tkinter as _tk2
        self._module_canvas = _tk2.Canvas(scroll_outer, highlightthickness=0)
        module_scrollbar = ttk.Scrollbar(scroll_outer, orient='vertical',
                                          command=self._module_canvas.yview)
        self._module_scrollable = ttk.Frame(self._module_canvas)

        self._module_scrollable.bind(
            '<Configure>',
            lambda e: self._module_canvas.configure(
                scrollregion=self._module_canvas.bbox('all')),
        )
        self._canvas_window_id = self._module_canvas.create_window(
            (0, 0), window=self._module_scrollable, anchor='nw',
        )
        # 内层 Frame 宽度跟随 Canvas（实现 fill='x'）
        self._module_canvas.bind('<Configure>', self._on_canvas_configure)
        self._module_canvas.configure(yscrollcommand=module_scrollbar.set)

        self._module_canvas.pack(side='left', fill='both', expand=True)
        module_scrollbar.pack(side='right', fill='y')

        # 绑定鼠标滚轮
        def _on_mousewheel(event):
            self._module_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        self._module_canvas.bind('<Enter>',
                                  lambda e: self._module_canvas.bind_all('<MouseWheel>', _on_mousewheel))
        self._module_canvas.bind('<Leave>',
                                  lambda e: self._module_canvas.unbind_all('<MouseWheel>'))

        # 遍历模块
        from modules import list_modules
        registry = list_modules()
        if not registry:
            ttk.Label(self._module_scrollable, text=_('module.status.unloaded')).pack()
        else:
            for name in registry:
                self._build_module_section(self._module_scrollable, name, registry[name])

        # ── 底部按钮 ──
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill='x', pady=(12, 0))
        ttk.Button(btn_frame, text=_('config.save'),
                   command=self._on_save).pack(side='right', padx=(6, 0))
        ttk.Button(btn_frame, text=_('config.cancel'),
                   command=self._on_close).pack(side='right')

    def _refresh_lang_list(self) -> None:
        """刷新语言下拉列表"""
        langs = self._i18n.available_languages
        items = []
        self._lang_map = {}  # display name -> code
        for lang in langs:
            label = _(f'config.language.builtin', name=lang['name'])
            items.append(label)
            self._lang_map[label] = lang['code']
        items.append(_('config.language.custom'))
        self._lang_combo['values'] = items

        # 定位当前语言
        current = self._i18n.current_language
        for label, code in self._lang_map.items():
            if code == current:
                self._lang_combo.set(label)
                break
        else:
            self._lang_combo.set(items[0] if items else '')

    def _on_language_selected(self, event=None) -> None:
        """语言下拉切换"""
        selected = self._lang_combo.get()
        if selected == _('config.language.custom'):
            self._browse_lang_file()
            return
        code = self._lang_map.get(selected, 'en')
        self._i18n.set_language(code)
        self._refresh_all_texts()

    def _browse_lang_file(self) -> None:
        """浏览自定义语言文件"""
        path = filedialog.askopenfilename(
            title=_('config.language.browse'),
            filetypes=[('Language files', '*.jsonc *.json'), ('All files', '*.*')],
        )
        if path:
            self._i18n.set_language(path)
            self._refresh_lang_list()
            self._refresh_all_texts()

    # ── 模块区域 ──────────────────────────────────────────

    def _on_canvas_configure(self, event) -> None:
        """Canvas 尺寸变化时更新内层 Frame 宽度以匹配"""
        try:
            self._module_canvas.itemconfig(self._canvas_window_id, width=event.width)
        except Exception:
            pass

    def _build_module_section(self, parent: ttk.Frame, name: str, cls) -> None:
        """构建单个模块的配置区域（可折叠）"""
        frame = ttk.LabelFrame(parent, padding=4)
        frame.pack(fill='x', pady=(0, 6))

        # ── 标题行（始终可见） ──
        header = ttk.Frame(frame)
        header.pack(fill='x')

        info = self._manager.config_mgr.get_module_info(name)

        # 折叠切换标签（▼ 展开 / ▶ 折叠）
        # 默认展开：新窗口默认显示所有内容
        collapsed = False
        toggle_text = StringVar(value='▼ ')

        def _toggle():
            nonlocal collapsed
            collapsed = not collapsed
            toggle_text.set('▶ ' if collapsed else '▼ ')
            if collapsed:
                content_frame.pack_forget()
            else:
                content_frame.pack(fill='x', pady=(4, 0))

        toggle_label = ttk.Label(header, textvariable=toggle_text,
                                  cursor='hand2')
        toggle_label.pack(side='left')
        name_label = ttk.Label(header, text=cls.display_name,
                               cursor='hand2')
        name_label.pack(side='left')
        # 点击切换标签、模块名称、标题空白区域均触发折叠
        toggle_label.bind('<Button-1>', lambda e: _toggle())
        name_label.bind('<Button-1>', lambda e: _toggle())

        # 启用开关
        enabled_var = BooleanVar(value=info.get('enabled', True))
        cb = ttk.Checkbutton(header, text='', variable=enabled_var)
        cb.pack(side='left', padx=(8, 0))

        # 状态标签
        status_label = ttk.Label(header, foreground='gray')
        status_label.pack(side='right')
        self._status_labels[name] = status_label
        self._update_status_label(name)

        # ── 可折叠内容区域 ──
        content_frame = ttk.Frame(frame)
        content_frame.pack(fill='x', pady=(4, 0))

        # 自动启动
        auto_var = BooleanVar(value=info.get('auto_start', False))
        auto_cb = ttk.Checkbutton(content_frame, text=_('module.auto_start'),
                                  variable=auto_var)
        auto_cb.pack(anchor='w')

        # 模块配置 UI
        instance = self._manager.module_mgr.get_instance(name)
        if instance:
            config_frame, on_lang_cb = instance.build_config_frame(content_frame)
            config_frame.pack(fill='x', pady=(4, 0))
        else:
            # 未加载的模块也提供配置 UI
            config_frame = ttk.Frame(content_frame)
            ttk.Label(config_frame, text=_('module.no_config')).pack()
            on_lang_cb = lambda i18n: None

        # 刷新按钮
        ttk.Button(content_frame, text='↻',
                   command=lambda n=name: self._refresh_module(n)).pack(anchor='e')

        self._module_frames[name] = (enabled_var, auto_var, config_frame, on_lang_cb, content_frame, _toggle)

    def _update_status_label(self, name: str) -> None:
        """更新模块状态标签"""
        label = self._status_labels.get(name)
        if not label:
            return
        instance = self._manager.module_mgr.get_instance(name)
        if not instance:
            label.config(text=f'[{_("module.status.unloaded")}]')
            return
        status = instance.get_status()
        status_text = {
            'running': _('module.status.running'),
            'stopped': _('module.status.stopped'),
            'error': _('module.status.error'),
        }.get(status.state, status.state)
        color = {
            'running': 'green',
            'stopped': 'gray',
            'error': 'red',
        }.get(status.state, 'gray')
        label.config(text=f'[{status_text}]', foreground=color)

    def _refresh_module(self, name: str) -> None:
        """刷新单个模块状态"""
        self._update_status_label(name)

    # ── 语言刷新 ──────────────────────────────────────────

    def _refresh_all_texts(self) -> None:
        """语言切换后刷新所有文本"""
        if not self._window or not self._window.winfo_exists():
            return

        # 窗口标题
        self._window.title(_('config.title'))

        # 重建语言列表（下拉选项中包含翻译文本，需要重建）
        current_lang = self._lang_var.get()
        self._refresh_lang_list()

        # 刷新模块状态标签
        for name in self._status_labels:
            self._update_status_label(name)

        # 通知模块框架语言变化
        for name, (_, _, _, on_lang_cb, _, _) in self._module_frames.items():
            try:
                on_lang_cb(self._i18n)
            except Exception as e:
                logging.error(f'刷新模块语言失败 ({name}): {e}')

        # 刷新托盘菜单和 tooltip
        try:
            self._manager.refresh_tray()
        except Exception as e:
            logging.error(f'刷新托盘语言失败: {e}')

    # ── 保存与关闭 ───────────────────────────────────────

    def _on_save(self) -> None:
        """保存所有配置"""
        try:
            # 全局设置
            self._manager.config_mgr.set_manager_config({
                'auto_start': self._auto_start_var.get(),
                'language': self._i18n.current_language,
            })

            # 模块设置（先保存 config 再处理 enabled/auto_start）
            for name, _ in self._module_frames.items():
                instance = self._manager.module_mgr.get_instance(name)
                if instance:
                    module_config = instance.get_config_from_ui()
                    if module_config:
                        self._manager.config_mgr.set_module_config(name, module_config)

            for name, (enabled_var, auto_var, _, _, _, _) in self._module_frames.items():
                old_info = self._manager.config_mgr.get_module_info(name)
                # 启用切换：立即生效（启动/停止模块）
                new_enabled = enabled_var.get()
                old_enabled = old_info.get('enabled', True)
                if old_enabled != new_enabled:
                    if new_enabled:
                        self._manager.module_mgr.start_module(name)
                    else:
                        self._manager.module_mgr.stop_module(name)
                    self._manager.config_mgr.set_module_enabled(name, new_enabled)
                # 随启动加载：仅持久化，不影响当前状态
                self._manager.config_mgr.set_module_auto_start(name, auto_var.get())

            # 重新同步自动启动
            self._manager._init_auto_start()

            logging.info('配置已保存')

            # 刷新托盘状态（语言、模块启停立刻反映到菜单）
            self._manager.refresh_tray()

            self._destroy_window()
        except Exception as e:
            logging.error(f'保存配置失败: {e}', exc_info=True)
            messagebox.showerror(_('config.title'), f'Save failed: {e}')

    def _destroy_window(self) -> None:
        """销毁窗口（使下次 show 完全重建，避免 withold 后 lift 不可用）"""
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
            self._module_frames = {}
            self._status_labels = {}

    def _on_close(self) -> None:
        """关闭窗口（不保存）"""
        self._destroy_window()
