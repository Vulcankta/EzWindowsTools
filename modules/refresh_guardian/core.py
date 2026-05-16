"""
RefreshRateGuardian — 纯监控逻辑（无 UI、无配置文件 I/O）
"""

import copy
import logging
import time
from datetime import datetime
from typing import Callable, Optional

from utils.display_manager import (
    get_connected_displays,
    get_current_settings,
    get_supported_refresh_rates,
    set_refresh_rate,
)
from module_base import ModuleStatus


class RefreshRateGuardianCore:
    """显示器刷新率守护核心逻辑

    不管理线程，不处理配置文件 I/O。所有副作用通过回调函数委派。
    """

    def __init__(
        self,
        config: dict,
        on_correction: Optional[Callable[[list[str]], None]] = None,
    ) -> None:
        self._config: dict = config
        self._on_correction: Optional[Callable[[list[str]], None]] = on_correction
        self.last_check_time: Optional[datetime] = None
        self.last_values: dict[str, int] = {}
        self.last_corrections: list[str] = []
        # 修正冷却：防止 ChangeDisplaySettingsExW 频繁触发屏幕闪烁
        self._last_correction_time: float = 0.0
        self._cooldown_seconds: int = 300  # 默认 5 分钟冷却

    def update_config(self, config: dict) -> None:
        """热重载配置"""
        self._config = config

    def run_check(self) -> ModuleStatus:
        """执行一次检查，发现偏离时自动修正

        返回 ModuleStatus:
            - running: 检查正常或已修正
            - error:   检查过程发生异常
        """
        try:
            config = copy.deepcopy(self._config)
            displays_config: dict = config.get('displays', {})

            # 获取已连接显示器的当前刷新率
            connected = get_connected_displays()
            if not connected:
                self.last_check_time = datetime.now()
                return ModuleStatus(
                    state='running',
                    detail='未检测到显示器',
                    last_check=self.last_check_time,
                )

            # 保存当前值到快照
            self.last_values = {}
            for display in connected:
                self.last_values[display['name']] = display['current_refresh_rate']

            # 检查每个已记录的显示器
            show_notifications = config.get('show_notifications', True)
            corrections: list[str] = []
            for display in connected:
                name = display['name']
                friendly = display['friendly_name']
                current_hz = display['current_refresh_rate']

                entry = displays_config.get(name)
                if not entry:
                    continue  # 未记录此显示器

                expected_hz = entry.get('expected_refresh_rate', 0)
                entry_enabled = entry.get('enabled', True)

                if not entry_enabled or expected_hz <= 0:
                    continue  # 已禁用或期望值不合法

                if current_hz != expected_hz:
                    # 冷却检查：上次修正后未满冷却期则跳过（避免频繁闪屏）
                    cooldown = config.get('correction_cooldown_seconds', self._cooldown_seconds)
                    elapsed = time.monotonic() - self._last_correction_time
                    if elapsed < cooldown:
                        logging.debug(f'{friendly} 修正冷却中 ({cooldown - elapsed:.0f}s 剩余)')
                        continue
                    # 尝试修正
                    success, msg = set_refresh_rate(name, expected_hz)
                    if success:
                        corrections.append(f'{friendly} {current_hz}→{expected_hz}Hz')
                        self._last_correction_time = time.monotonic()
                    else:
                        corrections.append(f'{friendly} 修正失败: {msg}')

            self.last_corrections = corrections
            self.last_check_time = datetime.now()

            if corrections:
                msg = '修正完成: ' + ', '.join(corrections)
                logging.warning(msg)

                if show_notifications and self._on_correction:
                    self._on_correction(corrections)

                return ModuleStatus(
                    state='running',
                    detail='; '.join(corrections),
                    last_check=self.last_check_time,
                    was_corrected=True,
                )

            return ModuleStatus(
                state='running',
                detail='正常',
                last_check=self.last_check_time,
            )

        except Exception as e:
            logging.error(f'刷新率检查失败: {e}', exc_info=True)
            return ModuleStatus(
                state='error',
                detail=str(e),
                last_check=datetime.now(),
            )

    def get_supported_rates(self, name: str, width: int, height: int) -> list[int]:
        """获取指定显示器在给定分辨率下支持的刷新率列表"""
        try:
            return get_supported_refresh_rates(name, width, height)
        except Exception as e:
            logging.warning(f'获取支持刷新率失败 ({name}): {e}')
            return []
