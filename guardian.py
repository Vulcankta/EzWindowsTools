"""
ScreenTimeoutGuardian
--------------------
系統托盤工具 — 定時檢查 AC/DC 熄屏與睡眠超時，
若被其他軟體篡改則自動修正回預設值。

使用方式：
    python guardian.py
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

from power_manager import PowerManager

# ── 常數 ──────────────────────────────────────────────────
SECONDS_PER_MINUTE = 60
MIN_CHECK_INTERVAL = 5  # 最小檢查間隔（秒）

# ── 路徑 ──────────────────────────────────────────────────
# 支援 PyInstaller 打包：exe 時 config/log 跟 exe 同目錄
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'
CONFIG_BACKUP_FILE = BASE_DIR / 'config.json.bak'
LOG_FILE = BASE_DIR / 'guardian.log'

# ── 預設設定 ──────────────────────────────────────────────
DEFAULT_CONFIG = {
    "check_interval_seconds": 30,
    "ac_display_timeout_minutes": 10,
    "dc_display_timeout_minutes": 5,
    "ac_sleep_timeout_minutes": 30,
    "dc_sleep_timeout_minutes": 15,
    "show_notifications": True,
}

# ── JSONC 註解移除 ──────────────────────────────────────

def _strip_jsonc(text: str) -> str:
    """移除 JSONC 註解（// 和 /* */），支援字串內保留原文"""
    result: list[str] = []
    in_str = False
    str_char: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            result.append(ch)
            if ch == '\\':
                i += 1
                if i < len(text):
                    result.append(text[i])
            elif ch == str_char:
                in_str = False
                str_char = None
        else:
            if ch == '"':
                in_str = True
                str_char = ch
                result.append(ch)
            elif ch == '/' and i + 1 < len(text):
                if text[i + 1] == '/':
                    i += 2
                    while i < len(text) and text[i] != '\n':
                        i += 1
                    continue
                elif text[i + 1] == '*':
                    i += 2
                    while i + 1 < len(text) and not (text[i] == '*' and text[i + 1] == '/'):
                        i += 1
                    i += 2
                    continue
                else:
                    result.append(ch)
            else:
                result.append(ch)
        i += 1
    return ''.join(result)


# ── 設定鍵名清單（集中管理，避免重複字串散布） ────────
CONFIG_KEYS = {
    'ac_display': ('ac_display_timeout_minutes', 'AC 熄屏'),
    'dc_display': ('dc_display_timeout_minutes', 'DC 熄屏'),
    'ac_sleep':   ('ac_sleep_timeout_minutes',   'AC 睡眠'),
    'dc_sleep':   ('dc_sleep_timeout_minutes',   'DC 睡眠'),
}

# 寫入方法對應表（避免 getattr 動態查找，編譯期即可檢查完整性）
_WRITE_METHODS = {
    'ac_display': PowerManager.write_ac_display_timeout,
    'dc_display': PowerManager.write_dc_display_timeout,
    'ac_sleep':   PowerManager.write_ac_sleep_timeout,
    'dc_sleep':   PowerManager.write_dc_sleep_timeout,
}


def load_config() -> dict:
    """載入 config.json，若不存在則以預設值建立。

    若檔案損毀（JSON 解析失敗），先備份再寫入預設值。
    """
    if CONFIG_FILE.exists():
        try:
            raw = CONFIG_FILE.read_text(encoding='utf-8')
            cleaned = _strip_jsonc(raw)
            cfg = json.loads(cleaned)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f'設定檔損毀，備份後使用預設值: {e}')
            try:
                shutil.copy2(CONFIG_FILE, CONFIG_BACKUP_FILE)
                logging.info(f'已備份損毀的設定檔至 {CONFIG_BACKUP_FILE}')
            except OSError as backup_err:
                logging.error(f'備份設定檔失敗: {backup_err}')
    # 寫入預設設定
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    return dict(DEFAULT_CONFIG)


# ── 圖示產生 ──────────────────────────────────────────────

def _create_tray_icon_image(color: str, size: int = 64) -> Image.Image:
    """產生一個簡單的圓形托盤圖示"""
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
        logging.debug('無法在托盤圖示上繪製文字，使用純色圓圈')
    return img


ICON_GREEN = _create_tray_icon_image('#4CAF50')   # 正常
ICON_RED   = _create_tray_icon_image('#F44336')   # 剛剛修正
ICON_GRAY  = _create_tray_icon_image('#9E9E9E')   # 錯誤


# ── 守護程式主體 ──────────────────────────────────────────

class ScreenTimeoutGuardian:
    """系統托盤守護程式"""

    def __init__(self):
        self.config = load_config()
        self.pm = PowerManager()

        # 執行期狀態
        self._running = True
        self._lock = threading.Lock()  # 保護 _check_and_correct 避免競態

        # 記錄上次讀到的值（用於 tooltip 顯示）
        self._last_values: dict[str, int] = {}
        self._last_expected: dict[str, int] = {}
        self._last_corrections: list[str] = []

        # 建立托盤圖示
        self.icon = pystray.Icon(
            'ScreenTimeoutGuardian',
            ICON_GREEN,
            'ScreenTimeoutGuardian\n啟動中...',
            menu=pystray.Menu(
                pystray.MenuItem('立即檢查', self._check_now, default=True),
                pystray.MenuItem('編輯設定', self._edit_config),
                pystray.MenuItem('開啟紀錄', self._open_log),
                pystray.MenuItem('重新載入設定', self._reload_config),
                pystray.MenuItem('結束', self._exit_app),
            ),
        )

        # ── logging ──
        logging.basicConfig(
            filename=LOG_FILE,
            level=logging.INFO,
            format='%(asctime)s | %(levelname)-7s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            encoding='utf-8',
        )
        logging.info('═' * 50)
        logging.info('ScreenTimeoutGuardian 啟動')
        logging.info(f'檢查間隔: {self._get_safe_interval()}s')
        self._log_config_summary()

    # ── 啟動 ──────────────────────────────────────────────

    def run(self):
        """啟動監控執行緒 + 托盤圖示迴圈"""
        monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor.start()
        self.icon.run()

    # ── 設定輔助 ──────────────────────────────────────────

    def _get_safe_interval(self) -> int:
        """取得安全的檢查間隔（不小於最小值）"""
        return max(self.config.get('check_interval_seconds', 30), MIN_CHECK_INTERVAL)

    def _log_config_summary(self):
        """記錄當前設定摘要"""
        parts = []
        for key, (cfg_key, name) in CONFIG_KEYS.items():
            parts.append(f'{name}={self.config.get(cfg_key, 0)}min')
        logging.info('預設: ' + ' / '.join(parts))

    # ── 監控迴圈 ──────────────────────────────────────────

    def _monitor_loop(self):
        while self._running:
            try:
                self._check_and_correct()
            except Exception as e:
                logging.error(f'檢查過程發生例外: {e}')
                try:
                    self.icon.icon = ICON_GRAY
                    self.icon.title = f'ScreenTimeoutGuardian\n錯誤: {e}'
                except Exception:
                    pass  # 圖示更新失敗不應導致執行緒死亡
            try:
                interval = self._get_safe_interval()
                # 分段 sleep 以能更快響應 shutdown
                for _ in range(interval):
                    if not self._running:
                        break
                    time.sleep(1)
            except Exception as e:
                logging.error(f'sleep 異常: {e}')
                time.sleep(MIN_CHECK_INTERVAL)  # 降級
        logging.info('監控執行緒結束')

    def _check_and_correct(self):
        """執行一次檢查並修正（執行緒安全）"""
        if not self._lock.acquire(blocking=False):
            logging.debug('上一次檢查尚未完成，跳過本次')
            return
        try:
            self._do_check()
        finally:
            self._lock.release()

    def _do_check(self):
        """實際檢查邏輯（已受鎖保護）"""
        # 建立 config snapshot，避免 reload_config 中途換掉 self.config
        config = dict(self.config)

        scheme = self.pm.get_active_scheme()

        # 讀取四個值 (秒)
        values = {
            'ac_display': self.pm.read_ac_display_timeout(scheme),
            'dc_display': self.pm.read_dc_display_timeout(scheme),
            'ac_sleep':   self.pm.read_ac_sleep_timeout(scheme),
            'dc_sleep':   self.pm.read_dc_sleep_timeout(scheme),
        }

        # 期望值 (分鐘→秒)
        def _expected_seconds(key: str) -> int:
            cfg_key, _ = CONFIG_KEYS[key]
            return config.get(cfg_key, 0) * SECONDS_PER_MINUTE

        expected = {key: _expected_seconds(key) for key in values}

        # 比對
        needs_apply = False
        corrections = []
        log_lines = [f'檢查 [{datetime.now():%H:%M:%S}]']
        for key in values:
            v, e = values[key], expected[key]
            name = CONFIG_KEYS[key][1]
            log_lines.append(f'  {name}: {v}s (期望 {e}s)')
            if v != e:
                logging.warning(
                    f'{name} 被修改: 當前 {v}s ≠ 期望 {e}s，準備修正'
                )
                _WRITE_METHODS[key](scheme, e)
                needs_apply = True
                corrections.append(name)

        logging.info('  |  '.join(log_lines))

        # 更新顯示狀態
        self._last_values = values
        self._last_expected = expected
        self._last_corrections = corrections

        if needs_apply:
            self.pm.apply_scheme(scheme)
            msg = '修正完成: ' + ', '.join(corrections)
            logging.warning(msg)
            self.icon.icon = ICON_RED

            if config.get('show_notifications', True):
                self.icon.notify(
                    'ScreenTimeoutGuardian',
                    f'已自動修正: {", ".join(corrections)}',
                )
        else:
            self.icon.icon = ICON_GREEN

        self._update_tooltip(needs_apply)

    def _format_minutes(self, seconds: int) -> str:
        """將秒數格式化為使用者易讀的分鐘數"""
        if seconds == 0:
            return '永不'
        return f'{seconds // SECONDS_PER_MINUTE}分'

    def _update_tooltip(self, needs_apply: bool):
        """更新托盤 tooltip 顯示"""
        tip_lines = ['ScreenTimeoutGuardian — 監控中']
        tip_lines.append(f'檢查: {datetime.now():%H:%M:%S}')
        for key in self._last_values:
            name = CONFIG_KEYS[key][1]
            v = self._last_values[key]
            e = self._last_expected.get(key, 0)
            tip_lines.append(
                f'{name}: {self._format_minutes(v)} (期望 {self._format_minutes(e)})'
            )
        if needs_apply:
            tip_lines.append(f'⚠ 剛剛已修正: {", ".join(self._last_corrections)}')
        self.icon.title = '\n'.join(tip_lines)

    # ── 功能選單回呼 ──────────────────────────────────────

    def _check_now(self):
        """立即執行一次檢查（背景執行）"""
        threading.Thread(target=self._check_and_correct, daemon=True).start()

    def _edit_config(self):
        """用記事本開啟 config.json"""
        try:
            subprocess.Popen(['notepad.exe', str(CONFIG_FILE)])
        except Exception as e:
            logging.error(f'無法開啟設定檔: {e}')

    def _open_log(self):
        """用記事本開啟 log 檔"""
        try:
            subprocess.Popen(['notepad.exe', str(LOG_FILE)])
        except Exception as e:
            logging.error(f'無法開啟紀錄檔: {e}')

    def _reload_config(self):
        """重新載入 config.json"""
        try:
            self.config = load_config()
            logging.info(f'設定已重新載入，間隔={self._get_safe_interval()}s')
            self._log_config_summary()
            self._check_now()
        except Exception as e:
            logging.error(f'重新載入設定失敗: {e}')

    def _exit_app(self):
        """結束程式"""
        logging.info('使用者要求結束')
        self._running = False
        self.icon.stop()


# ── 進入點 ──────────────────────────────────────────────────

if __name__ == '__main__':
    os.chdir(BASE_DIR)
    app = ScreenTimeoutGuardian()
    app.run()
