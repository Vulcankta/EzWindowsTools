"""
集中配置管理 — 线程安全、原子写入、模块热重载
"""

import json
import logging
import threading
import copy
from pathlib import Path
from typing import Any, Callable, Optional

from utils.jsonc import strip_jsonc

# ── 配置默认值 ──────────────────────────────────────────────
DEFAULT_MANAGER_CONFIG = {
    "auto_start": False,
    "language": "zh-CN",
}

# screen_guardian 模块默认配置（向后兼容旧版 standalone config）
DEFAULT_SCREEN_GUARDIAN_CONFIG = {
    "enabled": True,
    "auto_start": True,
    "config": {
        "check_interval_seconds": 30,
        "ac_display_timeout_minutes": 10,
        "dc_display_timeout_minutes": 5,
        "ac_sleep_timeout_minutes": 30,
        "dc_sleep_timeout_minutes": 15,
        "show_notifications": True,
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "manager": dict(DEFAULT_MANAGER_CONFIG),
    "modules": {
        "screen_guardian": dict(DEFAULT_SCREEN_GUARDIAN_CONFIG),
    },
}


class ConfigManager:
    """集中式配置管理器（线程安全）"""

    def __init__(self, config_path: Path) -> None:
        self._path = config_path.resolve()
        self._lock = threading.Lock()
        self._callbacks: dict[str, list[Callable[[str, dict], None]]] = {}
        # _callbacks 结构: {"module_name": [callback, ...]}
        # 回调签名: callback(module_name: str, config: dict) -> None
        # "__all__" 表示全局变更通知
        self._config = self._load()

    # ── 加载 ────────────────────────────────────────────────

    def _load(self) -> dict:
        """加载配置文件，不存在时用默认值创建"""
        if not self._path.exists():
            config = dict(DEFAULT_CONFIG)
            self._write_atomic(config)
            logging.info(f'已创建默认配置文件: {self._path}')
            return config

        try:
            raw = self._path.read_text(encoding='utf-8')
            cleaned = strip_jsonc(raw)
            config = json.loads(cleaned)
            config = self._migrate(config)
            return config
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f'配置文件损坏，使用默认值: {e}')
            return dict(DEFAULT_CONFIG)

    @staticmethod
    def _migrate(config: dict) -> dict:
        """从旧版格式迁移到新版集中式格式

        旧版检测: 顶层有旧版 key 且没有新版 key 'manager'。
        新版检测: 顶层有 'manager'，直接补全缺失字段。
        """
        # 严格旧版检测: 有旧版关键字且没有新版结构
        has_old_key = 'ac_display_timeout_minutes' in config
        has_new_structure = 'manager' in config

        if has_old_key and not has_new_structure:
            logging.info('检测到旧版配置文件，迁移至新版格式')
            old = config
            new: dict[str, Any] = {
                'manager': dict(DEFAULT_MANAGER_CONFIG),
                'modules': {},
            }
            sg_config = dict(DEFAULT_SCREEN_GUARDIAN_CONFIG)
            for key in ['check_interval_seconds',
                         'ac_display_timeout_minutes',
                         'dc_display_timeout_minutes',
                         'ac_sleep_timeout_minutes',
                         'dc_sleep_timeout_minutes',
                         'show_notifications']:
                if key in old:
                    sg_config['config'][key] = old[key]
            new['modules']['screen_guardian'] = sg_config
            return new

        # 确保必需的结构存在
        config.setdefault('manager', dict(DEFAULT_MANAGER_CONFIG))
        config.setdefault('modules', {})
        return config

    # ── 原子写入 ────────────────────────────────────────────

    def _write_atomic(self, config: dict) -> None:
        """原子写入：先写 .tmp 再 replace"""
        tmp_path = self._path.with_suffix('.json.tmp')
        try:
            tmp_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
            tmp_path.replace(self._path)
        except Exception:
            # 清理临时文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def _save(self) -> None:
        """保存当前配置到文件（已持有锁时调用）"""
        self._write_atomic(self._config)

    # ── 公开读取 ────────────────────────────────────────────

    def get_manager_config(self) -> dict:
        """获取管理器配置（深拷贝，线程安全）"""
        with self._lock:
            return copy.deepcopy(self._config.get('manager', {}))

    def get_module_info(self, name: str) -> dict:
        """获取模块完整配置（含 enabled/auto_start/config），线程安全"""
        with self._lock:
            default = {'enabled': True, 'auto_start': False, 'config': {}}
            info = self._config.get('modules', {}).get(name, {})
            merged = dict(default)
            merged.update(info)
            return copy.deepcopy(merged)

    def is_module_enabled(self, name: str) -> bool:
        with self._lock:
            info = self._config.get('modules', {}).get(name, {})
            return info.get('enabled', True)

    def is_module_auto_start(self, name: str) -> bool:
        with self._lock:
            info = self._config.get('modules', {}).get(name, {})
            return info.get('auto_start', False)

    # ── 写入 ────────────────────────────────────────────────

    def set_manager_config(self, config: dict) -> None:
        """更新管理器配置"""
        with self._lock:
            self._config['manager'].update(config)
            self._save()
        self._notify_all()

    def set_module_enabled(self, name: str, enabled: bool) -> None:
        """设置模块启用状态（仅持久化，不触发热重载 — 由调用方管理模块生命周期）"""
        with self._lock:
            self._config.setdefault('modules', {}).setdefault(name, {})
            self._config['modules'][name]['enabled'] = enabled
            self._save()

    def set_module_auto_start(self, name: str, auto_start: bool) -> None:
        """设置模块自动启动状态（仅持久化，不触发热重载）"""
        with self._lock:
            self._config.setdefault('modules', {}).setdefault(name, {})
            self._config['modules'][name]['auto_start'] = auto_start
            self._save()

    def set_module_config(self, name: str, module_config: dict) -> None:
        """设置模块的 config 部分并触发热重载"""
        with self._lock:
            self._config.setdefault('modules', {}).setdefault(name, {})
            self._config['modules'][name]['config'] = module_config
            self._save()
        self._notify(name)
        self._notify_global(name, self.get_module_info(name))

    # ── 回调管理 ────────────────────────────────────────────

    def register_callback(self, module_name: str,
                          callback: Callable[[str, dict], None]) -> None:
        """注册配置变更回调

        module_name:
            - 具体模块名: 仅该模块配置变化时触发
            - "__all__": 任何配置变化时触发
        回调签名: callback(module_name, module_config_info)
        """
        with self._lock:
            self._callbacks.setdefault(module_name, []).append(callback)

    def _notify(self, name: str) -> None:
        """通知指定模块的监听者（不含 __all__，线程安全：快照后释放锁再执行）"""
        config = self.get_module_info(name)
        with self._lock:
            cbs = list(self._callbacks.get(name, []))
        for cb in cbs:
            try:
                cb(name, config)
            except Exception as e:
                logging.error(f'配置回调异常 ({name}): {e}')

    def _notify_global(self, name: str = '', config: dict | None = None) -> None:
        """通知 __all__ 监听者

        参数:
            name:   触发变更的模块名（'' 表示全局变更）
            config: 变更的具体配置（None 时自动取管理器配置）
        """
        with self._lock:
            all_cbs = list(self._callbacks.get('__all__', []))
        if not all_cbs:
            return
        if config is None:
            config = self.get_manager_config()
        for cb in all_cbs:
            try:
                cb(name, config)
            except Exception as e:
                logging.error(f'全局配置回调异常: {e}')

    def _notify_all(self) -> None:
        """通知所有模块监听者 + 全局监听者（一次）"""
        with self._lock:
            names = [n for n in self._callbacks if n != '__all__']
        for name in names:
            self._notify(name)
        self._notify_global()

    # ── 重新加载 ────────────────────────────────────────────

    def reload(self) -> None:
        """从磁盘重新加载配置"""
        with self._lock:
            self._config = self._load()
        self._notify_all()
        logging.info('配置已从磁盘重新加载')
