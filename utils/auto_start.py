"""
Windows 开机自启动管理
主要: HKCU 注册表 RUN 键（无需管理员权限）
备选: 启动文件夹快捷方式
"""

import logging
import os
import sys
from pathlib import Path

APP_NAME = 'EzWindowsTools'


def get_registry_value_for(script_path: Path) -> str:
    """生成注册表值（带引号路径，支持空格）

    参数:
        script_path: 管理器脚本的完整路径
    """
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}"'
    python = Path(sys.executable).resolve()
    return f'"{python}" "{script_path.resolve()}"'


def is_registry_value_correct(expected_value: str) -> bool:
    """检查注册表中的值是否与期望值一致"""
    import winreg
    try:
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ) as key:
            actual, _ = winreg.QueryValueEx(key, APP_NAME)
            return actual == expected_value
    except FileNotFoundError:
        return False
    except (OSError, PermissionError):
        return _check_startup_shortcut()


def set_auto_start(enabled: bool,
                   script_path: Path | None = None) -> bool:
    """设置开机自启动，成功返回 True"""
    import winreg

    script_path_resolved = (script_path or Path(__file__)).resolve()
    value = get_registry_value_for(script_path_resolved)
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
                logging.info(f'已设置开机自启动: {value}')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    logging.info('已取消开机自启动')
                except FileNotFoundError:
                    pass  # 尚未设置
        return True
    except (OSError, PermissionError) as e:
        logging.warning(f'注册表自启动失败，尝试备选方案: {e}')
        return _fallback_startup_shortcut(enabled, script_path_resolved)


def is_auto_start() -> bool:
    """检查是否已设置开机自启动"""
    import winreg

    try:
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        pass
    except (OSError, PermissionError) as e:
        logging.debug(f'读取注册表自启动失败: {e}')

    return _check_startup_shortcut()


# ── 备选方案: 启动文件夹快捷方式 ───────────────────────────


def _get_startup_dir() -> Path:
    """获取当前用户的启动文件夹路径"""
    startup = Path(os.environ.get(
        'APPDATA',
        Path.home() / 'AppData' / 'Roaming',
    )) / r'Microsoft\Windows\Start Menu\Programs\Startup'
    return startup


def _get_batch_path() -> Path:
    return _get_startup_dir() / f'{APP_NAME}.bat'


def _fallback_startup_shortcut(enabled: bool,
                               script_path: Path | None = None) -> bool:
    """备选: 通过批处理文件实现开机自启动"""
    bat = _get_batch_path()
    if enabled:
        try:
            bat.parent.mkdir(parents=True, exist_ok=True)
            script_path_resolved = (script_path or Path(__file__)).resolve()
            value = get_registry_value_for(script_path_resolved)
            bat.write_text(f'@echo off\nstart "" {value}\n', encoding='utf-8')
            logging.info(f'已创建启动批处理: {bat}')
            return True
        except OSError as e:
            logging.error(f'创建启动批处理失败: {e}')
            return False
    else:
        try:
            if bat.exists():
                bat.unlink()
                logging.info(f'已删除启动批处理: {bat}')
            return True
        except OSError as e:
            logging.error(f'删除启动批处理失败: {e}')
            return False


def _check_startup_shortcut() -> bool:
    """检查启动文件夹中是否有我们的入口"""
    return _get_batch_path().exists()
