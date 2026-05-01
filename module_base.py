"""
模块抽象基类 — 所有管理器模块必须继承此基类
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import time


@dataclass
class ModuleStatus:
    """模块运行状态"""
    state: str = 'stopped'       # 'running' | 'stopped' | 'error'
    detail: str = ''             # 详细描述
    last_check: Optional[datetime] = None  # 最近一次检查时间


class ModuleBase(ABC):
    """所有管理器模块的抽象基类

    子类必须覆写以下类属性:
        name           — 模块标识符，如 'screen_guardian'
        display_name   — 人类可读名称，如 '屏幕超时守护'
        description    — 简短描述
    """
    name: str = ''
    display_name: str = ''
    description: str = ''

    def __init__(self) -> None:
        self._status = ModuleStatus(state='stopped')
        self._heartbeat: float = 0.0
        self._running = False
        self._check_interval: float = 60.0  # 用于 is_alive() 动态阈值

    # ── 生命周期 ──────────────────────────────────────────

    def start(self) -> None:
        """启动模块（安全包装，子类实现 _start）"""
        try:
            self._running = True
            self._start()
            self._heartbeat = time.monotonic()
            self._status = ModuleStatus(state='running')
        except Exception as e:
            self._running = False
            self._status = ModuleStatus(state='error', detail=str(e))
            raise

    @abstractmethod
    def _start(self) -> None:
        """子类实现的具体启动逻辑"""
        ...

    def stop(self, timeout: float = 5.0) -> bool:
        """停止模块（安全包装，子类实现 _stop）

        返回: 是否在超时前成功停止
        """
        try:
            self._running = False
            result = self._stop(timeout)
            if result:
                self._status = ModuleStatus(state='stopped')
            return result
        except Exception as e:
            self._status = ModuleStatus(state='error', detail=f'stop 失败: {e}')
            return False

    @abstractmethod
    def _stop(self, timeout: float) -> bool:
        """子类实现的具体停止逻辑"""
        ...

    # ── 状态 ──────────────────────────────────────────────

    @abstractmethod
    def get_status(self) -> ModuleStatus:
        """获取模块当前状态"""
        ...

    def is_alive(self, tolerance: float = 3.0) -> bool:
        """心跳检测: 运行中超时未更新心跳认为已死亡

        tolerance: 允许错过的检查周期数（默认 3 倍间隔）
        """
        if self._status.state == 'running':
            return (time.monotonic() - self._heartbeat) < (self._check_interval * tolerance)
        return False

    # ── 配置 ──────────────────────────────────────────────

    def set_initial_config(self, config: dict) -> None:
        """设置初始配置（在 start() 前调用）

        子类可覆写以接收管理器传递的初始配置。
        """
        pass

    @abstractmethod
    def on_config_changed(self, config: dict) -> None:
        """配置热重载回调（Manager 保存配置后调用）"""
        ...

    def get_config_from_ui(self) -> dict:
        """从 UI 控件获取当前配置值

        由 build_config_frame 的实现提供，默认返回空 dict。
        """
        return {}

    # ── UI ────────────────────────────────────────────────

    def build_config_frame(self, parent) -> tuple:
        """构建模块配置 UI 控件（阶段 1 手动构建）

        返回: (frame, on_language_change_callback)
            - frame: ttk.Frame 实例
            - callback: 语言切换时被调用，签名为 callback(i18n)
        """
        from tkinter import ttk
        frame = ttk.Frame(parent)
        ttk.Label(frame, text='此模块无配置项').pack()
        return frame, lambda i18n: None
