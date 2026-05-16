"""
模块生命周期管理器 — 发现、加载、启停、配置变更转发
"""

import logging
from typing import Optional

from module_base import ModuleBase
from modules import discover_modules, list_modules
from config_manager import ConfigManager


class ModuleManager:
    """模块生命周期管理"""

    def __init__(self, config_mgr: ConfigManager) -> None:
        self._config_mgr = config_mgr
        self._instances: dict[str, ModuleBase] = {}

        # 注册全局配置回调，确保模块热重载
        self._config_mgr.register_callback('__all__', self._on_any_config_changed)

    # ── 加载与启动 ───────────────────────────────────────

    def discover_and_load(self) -> dict[str, type[ModuleBase]]:
        """发现可用模块（不启动）"""
        return discover_modules()

    def start_all_auto(self) -> list[str]:
        """启动所有标记为 auto_start 的模块，返回成功列表"""
        registry = list_modules()
        # 预读全部配置快照，避免迭代中途配置变更导致不一致
        infos = {name: self._config_mgr.get_module_info(name) for name in registry}
        started: list[str] = []
        for name, cls in registry.items():
            info = infos.get(name, {})
            if info.get('auto_start', False):
                if self.start_module(name, cls, info):
                    started.append(name)
        return started

    def start_module(self, name: str,
                     cls: Optional[type[ModuleBase]] = None,
                     info: Optional[dict] = None) -> bool:
        """启动单个模块

        参数:
            name: 模块标识符
            cls: 模块类（可 None 让管理器自动查找）
            info: 预取配置信息（避免重复读取）
        """
        if name in self._instances:
            running = self._instances[name].get_status().state == 'running'
            if running:
                logging.debug(f'模块 {name} 已在运行')
                return True
            # 已停止，重新创建实例
            del self._instances[name]

        try:
            if cls is None:
                from modules import get_module
                cls = get_module(name)
                if cls is None:
                    raise ValueError(f'模块 {name} 未注册')

            if info is None:
                info = self._config_mgr.get_module_info(name)

            instance = cls()
            instance.set_initial_config(info.get('config', {}))
            instance.start()
            self._instances[name] = instance

            # 注册模块级配置热重载
            self._config_mgr.register_callback(name, self._on_module_config_changed)

            logging.info(f'模块已启动: {instance.display_name}')
            return True
        except Exception as e:
            logging.error(f'启动模块 {name} 失败: {e}')
            return False

    def stop_module(self, name: str, timeout: float = 5.0) -> bool:
        """停止单个模块"""
        instance = self._instances.get(name)
        if instance is None:
            return True
        try:
            result = instance.stop(timeout=timeout)
            if result:
                logging.info(f'模块已停止: {instance.display_name}')
            else:
                logging.warning(f'模块停止超时: {instance.display_name}')
            return result
        except Exception as e:
            logging.error(f'停止模块 {name} 失败: {e}')
            return False

    def stop_all(self, timeout: float = 5.0) -> None:
        """停止所有模块"""
        for name in list(self._instances):
            self.stop_module(name, timeout)
        self._instances.clear()

    # ── 查询 ─────────────────────────────────────────────

    def get_instance(self, name: str) -> Optional[ModuleBase]:
        """获取模块实例"""
        return self._instances.get(name)

    def is_running(self, name: str) -> bool:
        """检查模块是否在运行"""
        instance = self._instances.get(name)
        if instance is None:
            return False
        return instance.get_status().state == 'running'

    def get_running_modules(self) -> list[str]:
        """获取正在运行的模块列表"""
        return [
            name for name in self._instances
            if self._instances[name].get_status().state == 'running'
        ]

    # ── 配置变更回调 ─────────────────────────────────────

    def _on_module_config_changed(self, module_name: str, config: dict) -> None:
        """模块配置变更：热重载到对应的运行中实例"""
        instance = self._instances.get(module_name)
        if instance is None:
            return
        try:
            instance.on_config_changed(config.get('config', {}))
            logging.debug(f'配置已热重载: {module_name}')
        except Exception as e:
            logging.error(f'热重载配置失败 ({module_name}): {e}')

    def _on_any_config_changed(self, module_name: str, config: dict) -> None:
        """全局配置回调：暂不处理特殊逻辑"""
        pass
