"""
ScreenTimeoutGuardian — 独立运行版本
====================================
系统托盘工具，定时检查 AC/DC 熄屏与睡眠超时，被篡改时自动修正。

使用方式：
    python guardian.py

此为向后兼容的独立入口，内部引用模块化结构。
"""

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

from utils.jsonc import strip_jsonc
from modules.screen_guardian.core import ScreenGuardianCore, CONFIG_KEYS

# ── 路径 ──────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'
CONFIG_BACKUP_FILE = BASE_DIR / 'config.json.bak'
LOG_FILE = BASE_DIR / 'guardian.log'

# ── 预设设定 ──────────────────────────────────────────────
DEFAULT_CONFIG = {
    "check_interval_seconds": 30,
    "ac_display_timeout_minutes": 10,
    "dc_display_timeout_minutes": 5,
    "ac_sleep_timeout_minutes": 30,
    "dc_sleep_timeout_minutes": 15,
    "show_notifications": True,
}

MIN_CHECK_INTERVAL = 5


def load_config() -> dict:
    """加载 config.json，若不存在则以预设值建立。

    兼容新版管理器格式（嵌套在 modules.screen_guardian.config 下）。
    若文件损坏（JSON 解析失败），先备份再写入预设值。
    """
    if CONFIG_FILE.exists():
        try:
            raw = CONFIG_FILE.read_text(encoding='utf-8')
            cleaned = strip_jsonc(raw)
            cfg = json.loads(cleaned)
            # 检测新版管理器格式（顶层有 manager 键）
            if 'manager' in cfg:
                sg = cfg.get('modules', {}).get('screen_guardian', {})
                sg_cfg = sg.get('config', {})
                result = dict(DEFAULT_CONFIG)
                result.update(sg_cfg)
                return result
            # 旧版扁平格式
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f'设定档损坏，备份后使用预设值: {e}')
            try:
                shutil.copy2(CONFIG_FILE, CONFIG_BACKUP_FILE)
                logging.info(f'已备份损坏的设定档至 {CONFIG_BACKUP_FILE}')
            except OSError as backup_err:
                logging.error(f'备份设定档失败: {backup_err}')
    # 写入预设设定
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    return dict(DEFAULT_CONFIG)


# ── 图标生成 ──────────────────────────────────────────────

def _create_tray_icon_image(color: str, size: int = 64) -> Image.Image:
    """生成一个简单的圆形托盘图标"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline=None,
    )
    try:
        draw.text((size // 2, size // 2), 'Z', fill='white',
                  anchor='mm', font=None)
    except Exception:
        logging.debug('无法在托盘图标上绘制文字，使用纯色圆圈')
    return img


ICON_GREEN = _create_tray_icon_image('#4CAF50')   # 正常
ICON_RED   = _create_tray_icon_image('#F44336')   # 刚刚修正
ICON_GRAY  = _create_tray_icon_image('#9E9E9E')   # 错误


# ── 守护程序主体 ──────────────────────────────────────────

class ScreenTimeoutGuardian:
    """系统托盘守护程序（独立运行版本）"""

    def __init__(self):
        self.config = load_config()
        self._core = ScreenGuardianCore(
            config=self.config,
            on_correction=self._on_correction,
        )

        # 运行期状态
        self._running = True
        self._lock = threading.Lock()

        # 建立托盘图标
        self.icon = pystray.Icon(
            'ScreenTimeoutGuardian',
            ICON_GREEN,
            'ScreenTimeoutGuardian\n启动中...',
            menu=pystray.Menu(
                pystray.MenuItem('立即检查', self._check_now, default=True),
                pystray.MenuItem('编辑设定', self._edit_config),
                pystray.MenuItem('开启纪录', self._open_log),
                pystray.MenuItem('重新载入设定', self._reload_config),
                pystray.MenuItem('结束', self._exit_app),
            ),
        )

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
        logging.info('ScreenTimeoutGuardian 启动')
        logging.info(f'检查间隔: {self._get_safe_interval()}s')
        self._log_config_summary()

    # ── 启动 ──────────────────────────────────────────────

    def run(self):
        """启动监控线程 + 托盘图标循环"""
        monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor.start()
        self.icon.run()

    # ── 设定辅助 ──────────────────────────────────────────

    def _get_safe_interval(self) -> int:
        return max(self.config.get('check_interval_seconds', 30), MIN_CHECK_INTERVAL)

    def _log_config_summary(self):
        parts = []
        for cfg_key, name in CONFIG_KEYS.values():
            parts.append(f'{name}={self.config.get(cfg_key, 0)}min')
        logging.info('预设: ' + ' / '.join(parts))

    # ── 监控循环 ──────────────────────────────────────────

    def _monitor_loop(self):
        while self._running:
            try:
                self._check_and_correct()
            except Exception as e:
                logging.error(f'检查过程发生例外: {e}')
                try:
                    self.icon.icon = ICON_GRAY
                    self.icon.title = f'ScreenTimeoutGuardian\n错误: {e}'
                except Exception:
                    pass
            try:
                interval = self._get_safe_interval()
                for _ in range(interval):
                    if not self._running:
                        break
                    time.sleep(1)
            except Exception as e:
                logging.error(f'sleep 异常: {e}')
                time.sleep(MIN_CHECK_INTERVAL)

    def _check_and_correct(self):
        """执行一次检查并修正（线程安全）"""
        if not self._lock.acquire(blocking=False):
            logging.debug('上一次检查尚未完成，跳过本次')
            return
        try:
            self._do_check()
        finally:
            self._lock.release()

    def _do_check(self):
        """实际检查逻辑"""
        status = self._core.run_check()
        has_corrections = bool(self._core.last_corrections)

        if has_corrections:
            self.icon.icon = ICON_RED
            if self.config.get('show_notifications', True):
                self.icon.notify(
                    'ScreenTimeoutGuardian',
                    f'已自动修正: {", ".join(self._core.last_corrections)}',
                )
        else:
            self.icon.icon = ICON_GREEN

        self._update_tooltip()

    def _update_tooltip(self):
        """更新托盘 tooltip（委托给 core 的格式化逻辑）"""
        self.icon.title = '\n'.join(self._core.get_tooltip_lines())

    # ── 修正回调 ──────────────────────────────────────────

    def _on_correction(self, corrections: list[str]) -> None:
        """修正回调：由 core 在修正后调用"""
        logging.warning(f'修正完成: {", ".join(corrections)}')

    # ── 功能菜单回调 ──────────────────────────────────────

    def _check_now(self):
        threading.Thread(target=self._check_and_correct, daemon=True).start()

    def _edit_config(self):
        try:
            subprocess.Popen(['notepad.exe', str(CONFIG_FILE)])
        except Exception as e:
            logging.error(f'无法开启设定档: {e}')

    def _open_log(self):
        try:
            subprocess.Popen(['notepad.exe', str(LOG_FILE)])
        except Exception as e:
            logging.error(f'无法开启纪录档: {e}')

    def _reload_config(self):
        try:
            self.config = load_config()
            self._core.update_config(self.config)
            logging.info(f'设定已重新载入，间隔={self._get_safe_interval()}s')
            self._log_config_summary()
            self._check_now()
        except Exception as e:
            logging.error(f'重新载入设定失败: {e}')

    def _exit_app(self):
        logging.info('使用者要求结束')
        self._running = False
        self.icon.stop()


# ── 进入点 ──────────────────────────────────────────────────

if __name__ == '__main__':
    app = ScreenTimeoutGuardian()
    app.run()
