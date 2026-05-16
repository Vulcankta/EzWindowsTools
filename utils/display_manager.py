"""
Windows 显示设备管理 API 封装（ctypes + user32.dll）

提供:
- 枚举已连接的显示器
- 获取/设置指定显示器的刷新率
- 枚举指定显示器支持的刷新率列表
"""

import ctypes
import logging
from ctypes import wintypes
from typing import Optional


# ── Windows 常量 ─────────────────────────────────────────────

# EnumDisplaySettings 模式
ENUM_CURRENT_SETTINGS = -1
ENUM_REGISTRY_SETTINGS = -2

# DEVMODE.dmFields 标志
DM_BITSPERPEL        = 0x00040000
DM_PELSWIDTH         = 0x00080000
DM_PELSHEIGHT        = 0x00100000
DM_DISPLAYFLAGS      = 0x00200000
DM_DISPLAYFREQUENCY  = 0x00400000

# DISPLAY_DEVICE.StateFlags
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_MIRRORING_DRIVER    = 0x00000008

# ChangeDisplaySettingsEx 返回码
DISP_CHANGE_SUCCESSFUL    = 0
DISP_CHANGE_RESTART       = 1
DISP_CHANGE_FAILED        = -1
DISP_CHANGE_BADMODE       = -2
DISP_CHANGE_BADDUALVIEW   = -6

# ChangeDisplaySettingsEx 标志
CDS_TEST         = 0x00000002
CDS_UPDATEREGISTRY = 0x00000001

# EnumDisplaySettingsEx 标志
EDS_RAWMODE = 0x00000002  # 获取 GPU 驱动报告的所有模式（含显示器 EDID 未报告的）


# ── Windows 结构体 ───────────────────────────────────────────

class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ('cb',            ctypes.c_ulong),
        ('DeviceName',    ctypes.c_wchar * 32),
        ('DeviceString',  ctypes.c_wchar * 128),
        ('StateFlags',    ctypes.c_ulong),
        ('DeviceID',      ctypes.c_wchar * 128),
        ('DeviceKey',     ctypes.c_wchar * 128),
    ]


class DEVMODEW(ctypes.Structure):
    """Windows DEVMODE 结构体（仅包含显示器相关字段）"""
    _fields_ = [
        ('dmDeviceName',       ctypes.c_wchar * 32),
        ('dmSpecVersion',      ctypes.c_ushort),
        ('dmDriverVersion',    ctypes.c_ushort),
        ('dmSize',             ctypes.c_ushort),
        ('dmDriverExtra',      ctypes.c_ushort),
        ('dmFields',           ctypes.c_ulong),
        # union { 16 bytes（只定义第一种布局，不影响后续字段偏移）
        ('dmOrientation',      ctypes.c_short),
        ('dmPaperSize',        ctypes.c_short),
        ('dmPaperLength',      ctypes.c_short),
        ('dmPaperWidth',       ctypes.c_short),
        ('dmScale',            ctypes.c_short),
        ('dmCopies',           ctypes.c_short),
        ('dmDefaultSource',    ctypes.c_short),
        ('dmPrintQuality',     ctypes.c_short),
        # } end union
        ('dmColor',            ctypes.c_short),
        ('dmDuplex',           ctypes.c_short),
        ('dmYResolution',      ctypes.c_short),
        ('dmTTOption',         ctypes.c_short),
        ('dmCollate',          ctypes.c_short),
        ('dmFormName',         ctypes.c_wchar * 32),
        ('dmLogPixels',        ctypes.c_ushort),
        ('dmBitsPerPel',       ctypes.c_ulong),
        ('dmPelsWidth',        ctypes.c_ulong),
        ('dmPelsHeight',       ctypes.c_ulong),
        ('dmDisplayFlags',     ctypes.c_ulong),
        ('dmDisplayFrequency', ctypes.c_ulong),
        ('dmICMMethod',        ctypes.c_ulong),
        ('dmICMIntent',        ctypes.c_ulong),
        ('dmMediaType',        ctypes.c_ulong),
        ('dmDitherType',       ctypes.c_ulong),
        ('dmReserved1',        ctypes.c_ulong),
        ('dmReserved2',        ctypes.c_ulong),
        ('dmPanningWidth',     ctypes.c_ulong),
        ('dmPanningHeight',    ctypes.c_ulong),
    ]


# ── DLL 绑定 ─────────────────────────────────────────────────

_EnumDisplayDevicesW = ctypes.windll.user32.EnumDisplayDevicesW
_EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, ctypes.c_ulong,
                                 ctypes.POINTER(DISPLAY_DEVICEW), ctypes.c_ulong]
_EnumDisplayDevicesW.restype = wintypes.BOOL

_EnumDisplaySettingsExW = ctypes.windll.user32.EnumDisplaySettingsExW
_EnumDisplaySettingsExW.argtypes = [wintypes.LPCWSTR, ctypes.c_ulong,
                                    ctypes.POINTER(DEVMODEW), ctypes.c_ulong]
_EnumDisplaySettingsExW.restype = wintypes.BOOL

_ChangeDisplaySettingsExW = ctypes.windll.user32.ChangeDisplaySettingsExW
_ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW),
    wintypes.HWND, ctypes.c_ulong, wintypes.LPVOID,
]
_ChangeDisplaySettingsExW.restype = ctypes.c_long


# ── 公开 API ─────────────────────────────────────────────────

def get_connected_displays() -> list[dict]:
    """获取当前已连接的所有显示器信息

    返回:
        [{name, friendly_name, current_refresh_rate, current_width, current_height}, ...]
    """
    displays: list[dict] = []
    dd = DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    i = 0

    while _EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
        if dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP \
                and not (dd.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER):
            dm = DEVMODEW()
            dm.dmSize = ctypes.sizeof(DEVMODEW)
            if _EnumDisplaySettingsExW(dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
                displays.append({
                    'name': dd.DeviceName,
                    'friendly_name': dd.DeviceString,
                    'current_refresh_rate': dm.dmDisplayFrequency,
                    'current_width': dm.dmPelsWidth,
                    'current_height': dm.dmPelsHeight,
                })
        dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        i += 1

    return displays


def get_current_settings(name: str) -> Optional[dict]:
    """获取指定显示器的当前设置

    返回:
        {refresh_rate, width, height, bits_per_pel} 或 None
    """
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if not _EnumDisplaySettingsExW(name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        return None
    return {
        'refresh_rate': dm.dmDisplayFrequency,
        'width': dm.dmPelsWidth,
        'height': dm.dmPelsHeight,
        'bits_per_pel': dm.dmBitsPerPel,
    }


def get_supported_refresh_rates(name: str, width: int = 0, height: int = 0) -> list[int]:
    """枚举指定显示器支持的刷新率

    不传 width/height：返回所有分辨率下的全部刷新率
    传 width/height：   只返回该分辨率下的刷新率

    返回:
        [50, 59, 60, 75, 100, 120, ...] 去重升序
    """
    rates: set[int] = set()
    mode_index = 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        if not _EnumDisplaySettingsExW(name, mode_index, ctypes.byref(dm), EDS_RAWMODE):
            break
        if dm.dmDisplayFrequency > 0 and dm.dmPelsWidth > 0:
            if width > 0 and height > 0:
                if dm.dmPelsWidth == width and dm.dmPelsHeight == height:
                    rates.add(dm.dmDisplayFrequency)
            else:
                rates.add(dm.dmDisplayFrequency)
        mode_index += 1
    return sorted(rates)


def set_refresh_rate(name: str, target_hz: int) -> tuple[bool, str]:
    """设置指定显示器的刷新率

    返回:
        (成功?, 消息)
    """
    # 读取当前设置
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if not _EnumDisplaySettingsExW(name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        return False, '无法读取当前显示器设置'

    # 修改刷新率
    dm.dmFields = DM_BITSPERPEL | DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFLAGS | DM_DISPLAYFREQUENCY
    dm.dmDisplayFrequency = target_hz

    # 先测试
    result = _ChangeDisplaySettingsExW(
        name, ctypes.byref(dm), None, CDS_TEST, None,
    )
    if result == DISP_CHANGE_FAILED:
        return False, f'测试失败：不支持 {target_hz}Hz'
    if result == DISP_CHANGE_BADMODE:
        return False, f'不支持的模式：{target_hz}Hz'
    if result not in (DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART):
        return False, f'测试返回异常代码: {result}'

    # 应用
    result = _ChangeDisplaySettingsExW(
        name, ctypes.byref(dm), None, 0, None,
    )
    if result == DISP_CHANGE_SUCCESSFUL:
        return True, f'已设为 {target_hz}Hz'
    if result == DISP_CHANGE_RESTART:
        return True, f'需要重启以应用 {target_hz}Hz'
    return False, f'应用失败，代码: {result}'
