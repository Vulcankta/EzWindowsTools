"""
ScreenTimeoutGuardian — 纯监控逻辑（无 UI、无配置文件 I/O）
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# 确保项目根目录在 sys.path 中（仅在 power_manager 不可导入时）
try:
    from power_manager import PowerManager
except ImportError:
    _project_root = Path(__file__).resolve().parent.parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    from power_manager import PowerManager
from module_base import ModuleStatus

SECONDS_PER_MINUTE = 60

# ── 设定键名对照 ──────────────────────────────────────────
CONFIG_KEYS = {
    'ac_display': ('ac_display_timeout_minutes', 'AC 熄屏'),
    'dc_display': ('dc_display_timeout_minutes', 'DC 熄屏'),
    'ac_sleep':   ('ac_sleep_timeout_minutes',   'AC 睡眠'),
    'dc_sleep':   ('dc_sleep_timeout_minutes',   'DC 睡眠'),
}

# 写入方法对照表
_WRITE_METHODS = {
    'ac_display': PowerManager.write_ac_display_timeout,
    'dc_display': PowerManager.write_dc_display_timeout,
    'ac_sleep':   PowerManager.write_ac_sleep_timeout,
    'dc_sleep':   PowerManager.write_dc_sleep_timeout,
}

_READ_METHODS = {
    'ac_display': PowerManager.read_ac_display_timeout,
    'dc_display': PowerManager.read_dc_display_timeout,
    'ac_sleep':   PowerManager.read_ac_sleep_timeout,
    'dc_sleep':   PowerManager.read_dc_sleep_timeout,
}


class ScreenGuardianCore:
    """屏幕超时守护核心逻辑

    执行一次检查 + 修正，不管理线程和 UI。
    """

    def __init__(
        self,
        config: dict,
        on_correction: Optional[Callable[[list[str]], None]] = None,
    ):
        self._config = config
        self._on_correction = on_correction
        self._pm = PowerManager()

        # 上次检查的结果（用于外部查询）
        self.last_values: dict[str, int] = {}
        self.last_expected: dict[str, int] = {}
        self.last_corrections: list[str] = []
        self.last_check_time: Optional[datetime] = None

    def update_config(self, config: dict) -> None:
        """热更新配置（线程安全由调用方保证）"""
        self._config = config

    def run_check(self) -> ModuleStatus:
        """执行一次检查并修正，返回状态"""
        try:
            config = dict(self._config)  # 快照
            scheme = self._pm.get_active_scheme()

            # 读取当前值
            values: dict[str, int] = {}
            for key in CONFIG_KEYS:
                values[key] = _READ_METHODS[key](scheme)

            # 期望值（分钟→秒）
            expected: dict[str, int] = {}
            for key in CONFIG_KEYS:
                cfg_key, _ = CONFIG_KEYS[key]
                expected[key] = config.get(cfg_key, 0) * SECONDS_PER_MINUTE

            # 比对标修正
            corrections: list[str] = []
            for key in CONFIG_KEYS:
                v, e = values[key], expected[key]
                _, name = CONFIG_KEYS[key]
                if v != e:
                    logging.warning(
                        f'{name} 被修改: 当前 {v}s ≠ 期望 {e}s，准备修正'
                    )
                    _WRITE_METHODS[key](scheme, e)
                    corrections.append(name)

            # 保存上次结果
            self.last_values = values
            self.last_expected = expected
            self.last_corrections = corrections
            self.last_check_time = datetime.now()

            if corrections:
                # 套用变更
                self._pm.apply_scheme(scheme)
                msg = '修正完成: ' + ', '.join(corrections)
                logging.warning(msg)

                if config.get('show_notifications', True) and self._on_correction:
                    self._on_correction(corrections)

                return ModuleStatus(
                    state='running',
                    detail=f'已修正: {", ".join(corrections)}',
                    last_check=self.last_check_time,
                )

            return ModuleStatus(
                state='running',
                detail='正常',
                last_check=self.last_check_time,
            )

        except Exception as e:
            logging.error(f'检查失败: {e}')
            return ModuleStatus(
                state='error',
                detail=str(e),
                last_check=datetime.now(),
            )

    def _format_minutes(self, seconds: int) -> str:
        """秒数 → 可读分钟"""
        if seconds == 0:
            return '永不'
        return f'{seconds // SECONDS_PER_MINUTE}分'

    def get_tooltip_lines(self) -> list[str]:
        """生成托盘 tooltip 文本行（供独立模式使用）"""
        lines = ['ScreenTimeoutGuardian — 监控中']
        if self.last_check_time:
            lines.append(f'检查: {self.last_check_time:%H:%M:%S}')
        for key in CONFIG_KEYS:
            _, name = CONFIG_KEYS[key]
            v = self.last_values.get(key, 0)
            e = self.last_expected.get(key, 0)
            lines.append(
                f'{name}: {self._format_minutes(v)} (期望 {self._format_minutes(e)})'
            )
        if self.last_corrections:
            lines.append(f'⚠ 刚刚已修正: {", ".join(self.last_corrections)}')
        return lines
