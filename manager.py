"""
EzWindowsTools 管理器 — 模块化管理平台入口

启动流程:
    1. 加载集中配置
    2. 初始化国际化
    3. 发现可用模块
    4. 启动标记为 auto_start 的模块
    5. 处理开机自启动
    6. 启动系统托盘（后台线程）
    7. 主线程创建 tkinter 根窗口（隐藏），进入 mainloop
"""

import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config_manager import ConfigManager
from module_manager import ModuleManager
from modules import discover_modules
from utils.auto_start import set_auto_start, is_auto_start, is_registry_value_correct, get_registry_value_for
from utils.i18n import init as init_i18n, set_global as set_global_i18n

# ── 路径 ──────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()

CONFIG_FILE = BASE_DIR / 'config.json'
LOG_FILE = BASE_DIR / 'manager.log'
LOCALES_DIR = BASE_DIR / 'locales'


class Manager:
    """管理器 — 统筹配置、模块生命周期、托盘 UI 和配置窗口"""

    def __init__(self) -> None:
        self.config_mgr = ConfigManager(CONFIG_FILE)
        self.module_mgr = ModuleManager(self.config_mgr)

        # ── i18n ──
        self.i18n = init_i18n(LOCALES_DIR)
        set_global_i18n(self.i18n)
        lang = self.config_mgr.get_manager_config().get('language', 'zh-CN')
        self.i18n.set_language(lang)

        self._tray = None
        self._tray_thread: threading.Thread | None = None
        self._config_window = None
        self._root = None
        self._running = True

        # ── logging ──
        root_logger = logging.getLogger()
        if not root_logger.handlers:
            handler = RotatingFileHandler(
                LOG_FILE, maxBytes=1024 * 1024, backupCount=3,
                encoding='utf-8',
            )
            handler.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)-7s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            ))
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.INFO)
        logging.info('═' * 50)
        logging.info('EzWindowsTools 管理器启动')

    def run(self) -> None:
        """主入口：启动所有组件"""
        try:
            self._init_auto_start()
            self._init_modules()
            self._run_tray_and_ui()
        except Exception as e:
            logging.critical(f'管理器启动失败: {e}', exc_info=True)
            raise

    # ── 开机自启动 ──────────────────────────────────────

    def _init_auto_start(self) -> None:
        """同步开机自启动配置，含路径正确性校验"""
        mgr_config = self.config_mgr.get_manager_config()
        wanted = mgr_config.get('auto_start', False)
        current = is_auto_start()
        script_path = Path(__file__).resolve()

        if wanted:
            expected = get_registry_value_for(script_path)
            if not is_registry_value_correct(expected):
                set_auto_start(True, script_path)
                logging.info('已启用/更新开机自启动')
        elif not wanted and current:
            set_auto_start(False)
            logging.info('已禁用开机自启动')

    # ── 模块初始化 ──────────────────────────────────────

    def _init_modules(self) -> None:
        """发现模块并启动标记为 auto_start 的模块"""
        registry = discover_modules()
        logging.info(f'发现 {len(registry)} 个模块')
        for name, cls in registry.items():
            logging.info(f'  - {cls.display_name} ({name})')

        started = self.module_mgr.start_all_auto()
        if started:
            logging.info(f'已自动启动: {", ".join(started)}')

    # ── 托盘 + tkinter 主循环 ────────────────────────────

    def _run_tray_and_ui(self) -> None:
        """启动托盘（后台线程）+ tkinter 主循环（主线程）"""
        from ui.tray_manager import TrayManager

        self._tray = TrayManager(self)
        self._tray_thread = threading.Thread(
            target=self._tray.run,
            daemon=True,
            name='tray-thread',
        )
        self._tray_thread.start()
        logging.info('托盘图标已在后台线程启动')

        # 主线程运行 tkinter（窗口默认隐藏，设置菜单显示时弹出）
        import tkinter as tk
        self._root = tk.Tk()
        self._root.withdraw()  # 隐藏主窗口
        self._root.protocol('WM_DELETE_WINDOW', self._root.withdraw)
        self._root.mainloop()

    # ── 设置窗口 ──────────────────────────────────────────

    def open_settings(self) -> None:
        """打开设置窗口（线程安全：通过 root.after 调度到主线程）"""
        if self._root is None:
            return
        # 如果从非主线程调用（如托盘菜单回调），调度到主线程
        if threading.current_thread() is threading.main_thread():
            self._show_settings()
        else:
            self._root.after(0, self._show_settings)

    def _show_settings(self) -> None:
        """在主线程中显示设置窗口"""
        from ui.config_window import ConfigWindow
        if self._config_window is None:
            self._config_window = ConfigWindow(self)
        self._config_window.show()

    # ── 退出 ──────────────────────────────────────────────

    @property
    def root(self):
        """tkinter 根窗口（供 ConfigWindow 等使用）"""
        return self._root

    def refresh_tray(self) -> None:
        """刷新托盘图标（状态文本 + 菜单）"""
        if self._tray:
            self._tray._update_icon()

    def quit(self) -> None:
        """停止所有模块并退出"""
        logging.info('正在停止所有模块...')
        self._running = False

        # 停止模块
        self.module_mgr.stop_all(timeout=3.0)

        # 停止托盘并等待线程结束（避免在托盘线程自身中 self-join）
        if self._tray:
            self._tray.stop()
        if self._tray_thread and self._tray_thread.is_alive() \
                and threading.current_thread() is not self._tray_thread:
            self._tray_thread.join(timeout=2.0)

        # 退出 tkinter 主循环
        if self._root:
            try:
                self._root.quit()
            except Exception:
                pass

        logging.info('管理器已退出')


if __name__ == '__main__':
    app = Manager()
    app.run()
