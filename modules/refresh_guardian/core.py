"""
RefreshRateGuardian — 纯监控逻辑（无 UI、无配置文件 I/O）
"""

import copy
import logging
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
            msg = ''
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

                # 允许 1Hz 误差（NTSC vs 整数精度问题，如 59.94→59 或 119.88→119）
                # TODO: 360Hz+ 显示器 1Hz 仅 ±0.28%，可考虑百分比容差如 max(1, expected*0.01)
                if abs(current_hz - expected_hz) <= 1:
                    continue

                # 尝试修正
                success, msg = set_refresh_rate(name, expected_hz)
                if success:
                    corrections.append(f'{friendly} {current_hz}→{expected_hz}Hz')
                else:
                    corrections.append(f'{friendly} 修正失败: {msg}')

            self.last_corrections = corrections
            self.last_check_time = datetime.now()

            if corrections:
                msg = '\n'.join(corrections)
                logging.warning(msg)
                if show_notifications and self._on_correction:
                    self._on_correction(corrections)

            state = 'error' if any('失败' in c for c in corrections) else 'running'
            detail = msg if corrections else '检查正常'
            return ModuleStatus(
                state=state,
                detail=detail,
                last_check=self.last_check_time,
                was_corrected=bool(corrections),
            )
        except Exception as e:
            logging.warning(f'检查过程发生异常: {e}')
            return ModuleStatus(state='error', detail=str(e))
