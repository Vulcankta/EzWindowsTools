"""
模块自动发现与注册
"""

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Optional

from module_base import ModuleBase

_registry: dict[str, type[ModuleBase]] = {}


def discover_modules() -> dict[str, type[ModuleBase]]:
    """扫描 modules/ 目录，自动发现所有 ModuleBase 子类

    异常隔离: 单个模块导入失败不影响其他模块。
    支持 PyInstaller 冻结环境（回退到显式导入）。
    返回: {name: class} 字典
    """
    _registry.clear()
    pkg_dir = Path(__file__).parent

    if getattr(sys, 'frozen', False):
        _discover_frozen(pkg_dir)
    else:
        _discover_normal(pkg_dir)

    return dict(_registry)


def _discover_normal(pkg_dir: Path) -> None:
    """正常 Python 环境的模块发现（通过 pkgutil 扫描）"""
    for importer, mod_name, is_pkg in pkgutil.iter_modules([str(pkg_dir)]):
        if not is_pkg:
            continue
        _try_register_module(mod_name)


def _discover_frozen(pkg_dir: Path) -> None:
    """PyInstaller 冻结环境的模块发现

    pkgutil.iter_modules 无法遍历 zip 内容，改用目录扫描 + 显式导入。
    回退: 扫描打包时解压出的临时目录（sys._MEIPASS）。
    """
    # 尝试从 MEIPASS 目录扫描
    scan_paths = [pkg_dir]
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        mp = Path(meipass) / 'modules'
        if mp.is_dir():
            scan_paths.append(mp)

    seen = set()
    for base in scan_paths:
        if not base.is_dir():
            logging.debug(f'frozen 扫描路径不可用: {base}')
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / '__init__.py').exists():
                mod_name = entry.name
                if mod_name in seen:
                    continue
                seen.add(mod_name)
                _try_register_module(mod_name)


def _try_register_module(mod_name: str) -> None:
    """尝试导入并注册单个模块"""
    try:
        module = importlib.import_module(f'modules.{mod_name}')
    except Exception as e:
        logging.warning(f'跳过模块 {mod_name}: 导入失败 — {e}')
        return

    plugin_cls = getattr(module, 'ModulePlugin', None)
    if plugin_cls is None:
        logging.debug(f'modules/{mod_name} 未定义 ModulePlugin，跳过')
        return
    if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, ModuleBase)):
        logging.warning(f'modules/{mod_name} 的 ModulePlugin 不是 ModuleBase 子类')
        return

    mod_name_str: str = getattr(plugin_cls, 'name', mod_name)  # type: ignore[assignment]
    if mod_name_str in _registry:
        logging.warning(f'模块名 "{mod_name_str}" 冲突: {_registry[mod_name_str].__module__} vs {mod_name}')
        return

    _registry[mod_name_str] = plugin_cls
    logging.info(f'已发现模块: {plugin_cls.display_name} ({mod_name_str})')


def get_module(name: str) -> Optional[type[ModuleBase]]:
    """按名称获取模块类"""
    return _registry.get(name)


def list_modules() -> dict[str, type[ModuleBase]]:
    """列出所有已发现模块"""
    return dict(_registry)
