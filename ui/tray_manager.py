"""
托盘图标管理器 — 多模块状态聚合、右键菜单、i18n 感知
"""

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw
import pystray

from utils.i18n import _

if TYPE_CHECKING:
    from manager import Manager

# ── 图标颜色 ────────────────────────────────────────────────
COLOR_GREEN   = '#4CAF50'   # 全部正常
COLOR_RED     = '#F44336'   # 有模块刚修正
COLOR_GRAY    = '#9E9E9E'   # 有模块出错
COLOR_BLUE    = '#2196F3'   # 管理器启动中 / 无可用模块
COLOR_STOPPED = '#B0B0B0'   # 所有模块已停止


def _create_icon_image(color: str, size: int = 64) -> Image.Image:
    """生成圆形托盘图标"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
    )
    try:
        draw.text((size // 2, size // 2), 'E', fill='white',
                  anchor='mm', font=None)
    except Exception:
        pass
    return img


# 预生成各颜色图标
ICONS = {
    'ok':      _create_icon_image(COLOR_GREEN),
    'error':   _create_icon_image(COLOR_GRAY),
    'fixed':   _create_icon_image(COLOR_RED),
    'init':    _create_icon_image(COLOR_BLUE),
    'stopped': _create_icon_image(COLOR_STOPPED),
}


class TrayManager:
    """系统托盘管理器 — 负责托盘图标和菜单"""

    def __init__(self, manager: 'Manager') -> None:
        self._manager = manager
        self._icon: Optional[pystray.Icon] = None
        self._theme: str = 'init'
        self._last_status_text: str = f'{_("app.name")}\n...'
        self._tooltip_updates = 0

    # ── 构建菜单 ──────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        items = []

        # 模块列表
        from modules import list_modules
        registry = list_modules()
        if registry:
            for name in registry:
                cls = registry[name]
                enabled = self._manager.config_mgr.is_module_enabled(name)
                status = ''
                instance = self._manager.module_mgr.get_instance(name)
                if instance:
                    s = instance.get_status()
                    status = ' ●' if s.state == 'running' else (' ✕' if s.state == 'error' else ' ○')

                label = f'{"✓ " if enabled else "  "}{cls.display_name}{status}'
                items.append(
                    pystray.MenuItem(
                        label,
                        # pystray 将 Icon 对象作为第一个参数传给回调，用 *args 忽略
                        lambda *args, n=name: self._toggle_module(n),
                    )
                )

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem(_('tray.settings'), lambda *args: self._manager.open_settings()))
        items.append(pystray.MenuItem(_('tray.quit'), lambda *args: self._manager.quit()))
        return pystray.Menu(*items)

    def _toggle_module(self, name: str) -> None:
        """切换模块启用状态"""
        try:
            enabled = self._manager.config_mgr.is_module_enabled(name)
            if enabled:
                self._manager.module_mgr.stop_module(name)
            else:
                self._manager.module_mgr.start_module(name)
            self._manager.config_mgr.set_module_enabled(name, not enabled)
            self._update_icon()
        except Exception as e:
            logging.error(f'切换模块 {name} 失败: {e}', exc_info=True)
            # 备用反馈：即使 logging 失效，也通过托盘通知用户
            self._try_notify('切换失败', str(e)[:80])

    def _try_notify(self, title: str, message: str) -> None:
        """尝试发送托盘通知（安全版，不抛异常）"""
        try:
            if self._icon:
                self._icon.notify(title, message)
        except Exception:
            pass

    # ── 状态聚合 ──────────────────────────────────────────

    def _aggregate_status(self) -> tuple[str, str]:
        """聚合所有模块状态，返回颜色主题和 tooltip 文本"""
        from modules import list_modules
        registry = list_modules()
        if not registry:
            app_name = _('app.name')
            return 'init', f'{app_name}\n(no modules)'

        app_name = _('app.name')
        lines = [f'{app_name}']
        has_error = False
        has_fixed = False
        all_stopped = True

        for name in registry:
            instance = self._manager.module_mgr.get_instance(name)
            cls = registry[name]
            if not instance:
                lines.append(f'  ○ {cls.display_name}: {_("module.status.unloaded")}')
                continue

            status = instance.get_status()
            icon_char = {
                'running': '●',
                'error':   '✕',
                'stopped': '○',
            }.get(status.state, '?')

            lines.append(f'  {icon_char} {cls.display_name}: {status.detail}')

            if status.state == 'error':
                has_error = True
            elif status.was_corrected:
                has_fixed = True

            if status.state == 'running':
                all_stopped = False

        if has_error:
            theme = 'error'
        elif has_fixed:
            theme = 'fixed'
        elif all_stopped:
            theme = 'stopped'  # 全停止——有别于正常运行
        else:
            theme = 'ok'

        return theme, '\n'.join(lines)

    def _update_icon(self) -> None:
        """更新图标颜色和 tooltip"""
        if not self._icon:
            return

        try:
            theme, text = self._aggregate_status()
            self._theme = theme
            self._last_status_text = text

            self._icon.icon = ICONS.get(theme, ICONS['ok'])
            self._icon.title = text
            self._icon.menu = self._build_menu()
            self._tooltip_updates += 1
        except Exception as e:
            logging.error(f'更新托盘图标失败: {e}')

    # ── 生命周期 ──────────────────────────────────────────

    def run(self) -> None:
        """启动托盘图标（阻塞当前线程 — 应在后台线程调用）"""
        self._icon = pystray.Icon(
            'EzWindowsTools',
            ICONS['init'],
            f'{_("app.name")}\n...',
            menu=self._build_menu(),
        )
        logging.info('托盘图标已启动')
        assert self._icon is not None
        self._icon.run()

    def stop(self) -> None:
        """停止托盘图标"""
        if self._icon:
            try:
                self._icon.stop()
                logging.info('托盘图标已停止')
            except Exception as e:
                logging.error(f'停止托盘图标失败: {e}')
