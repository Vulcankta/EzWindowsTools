"""
EzWindowsTools 管理器 — 模块化管理平台入口

启动流程:
    1. 配置日志（必须先于任何 logging 调用）
    2. 加载集中配置
    3. 初始化国际化
    4. 发现可用模块
    5. 启动标记为 auto_start 的模块
    6. 处理开机自启动
    7. 启动系统托盘（后台线程）
    8. 主线程创建 tkinter 根窗口（隐藏），绑定跨线程事件，进入 mainloop
"""

import logging
import queue
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
    DATA_DIR = Path(sys._MEIPASS)  # PyInstaller onedir → _internal/
else:
    BASE_DIR = Path(__file__).parent.resolve()
    DATA_DIR = BASE_DIR

CONFIG_FILE = BASE_DIR / 'config.json'
LOG_FILE = BASE_DIR / 'manager.log'
LOCALES_DIR = DATA_DIR / 'locales'


def _setup_logging() -> None:
    """配置日志系统：强制替换所有 handler 为 RotatingFileHandler

    必须在任何 logging.info/warning/error 调用之前执行，
    否则 Python 的 logging 模块会自动调用 basicConfig() 添加 StreamHandler，
    导致后续的 handler 守卫检查失效。
    """
    root_logger = logging.getLogger()
    # 清空所有已有 handler（包括 basicConfig 自动添加的 StreamHandler）
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
        h.close()
    # 添加统一的 RotatingFileHandler
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


class Manager:
    """管理器 — 统筹配置、模块生命周期、托盘 UI 和配置窗口"""

    def __init__(self) -> None:
        # ═══ 必须先于任何 logging 调用 ═══
        _setup_logging()
        logging.info('═' * 50)
        logging.info('EzWindowsTools 管理器启动')

        self.config_mgr = ConfigManager(CONFIG_FILE)
        self.module_mgr = ModuleManager(self.config_mgr)
        self.module_mgr.set_notify_callback(self._module_notify)

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
        self._settings_queue: queue.Queue[bool] = queue.Queue()
        self._notify_queue: queue.Queue[tuple[str, str]] = queue.Queue()

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
        self._root.withdraw()
        self._root.protocol('WM_DELETE_WINDOW', self._root.withdraw)

        # 启动队列轮询 + 初始托盘状态刷新
        self._poll_settings_queue()
        # 托盘启动后延迟刷新一次状态（蓝色→实际颜色）
        self._root.after(500, self.refresh_tray)
        # 定期刷新托盘状态（每10秒），确保修正后图标能自动变回绿色
        self._root.after(10000, self._auto_refresh_tray)

        self._root.mainloop()

    # ── 设置窗口（线程安全：queue.Queue + tkinter.after 轮询）──

    def open_settings(self) -> None:
        """打开设置窗口（从任意线程调用，通过线程安全队列转发到主线程）"""
        try:
            self._settings_queue.put(True)
        except Exception as e:
            logging.error(f'open_settings 排队失败: {e}', exc_info=True)

    def _poll_settings_queue(self) -> None:
        """主线程轮询设置请求队列（通过 root.after 循环调用）

        queue.Queue 是线程安全的，root.after 保证此方法在主线程执行。

        注意: 必须捕获所有异常以确保 after() 始终被调度，
        任何未捕获的异常都会导致轮询永久停止、设置按钮失效。
        """
        try:
            while True:
                self._settings_queue.get_nowait()
                self._show_settings()
        except queue.Empty:
            pass
        except Exception as e:
            logging.error(f'设置队列轮询异常: {e}', exc_info=True)
        # 在同一轮询周期中处理通知队列
        self._poll_notify_queue()
        if self._root:
            self._root.after(200, self._poll_settings_queue)

    def _show_settings(self) -> None:
        """在主线程中显示设置窗口"""
        try:
            from ui.config_window import ConfigWindow
            if self._config_window is None:
                self._config_window = ConfigWindow(self)
            self._config_window.show()
        except Exception as e:
            logging.error(f'显示设置窗口失败: {e}', exc_info=True)

    # ── 退出 ──────────────────────────────────────────────

    def _module_notify(self, title: str, message: str) -> None:
        """模块通知回调：通过线程安全队列转发到主线程"""
        try:
            self._notify_queue.put((title, message))
        except Exception as e:
            logging.warning(f'模块通知排队失败: {e}')

    def _poll_notify_queue(self) -> None:
        """主线程轮询通知队列，发送托盘通知 + 刷新图标"""
        try:
            while True:
                title, message = self._notify_queue.get_nowait()
                if self._tray and self._tray._icon:
                    self._tray._icon.notify(title, message)
                self.refresh_tray()
        except queue.Empty:
            pass
        except Exception as e:
            logging.warning(f'通知队列处理失败: {e}')

    def _auto_refresh_tray(self) -> None:
        """定期（每10秒）刷新托盘状态，确保状态变化能被及时反映"""
        try:
            self.refresh_tray()
        except Exception as e:
            logging.warning(f'自动刷新托盘失败: {e}')
        if self._root:
            self._root.after(10000, self._auto_refresh_tray)

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
