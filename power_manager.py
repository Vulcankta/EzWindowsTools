"""
Windows 電源管理 API 封裝 (via ctypes)
提供讀取/寫入 AC/DC 熄屏與睡眠超時值的功能
"""

import ctypes
import ctypes.wintypes
from uuid import UUID

# ── 已知 GUID ──────────────────────────────────────────────
GUID_VIDEO_SUBGROUP          = UUID('{7516b95f-f776-4464-8c53-06167f40cc99}')
GUID_VIDEO_POWERDOWN_TIMEOUT = UUID('{3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e}')
GUID_SLEEP_SUBGROUP          = UUID('{238c9fa8-0aad-41ed-83f4-97be242c8f20}')
GUID_STANDBY_TIMEOUT         = UUID('{29f6c1db-86da-48c5-9fdb-f2b67b1f44da}')

ERROR_SUCCESS = 0


class GUID(ctypes.Structure):
    """Windows GUID 結構 (16 bytes, packed)"""
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]


def _uuid_to_guid(uuid_obj: UUID) -> GUID:
    """將 Python UUID 轉為 ctypes GUID 值"""
    return GUID.from_buffer_copy(uuid_obj.bytes_le)


# ── 快取 ctypes GUID (避免每次 API 呼叫都重新轉換) ──
_C_GUID_VIDEO_SUBGROUP          = _uuid_to_guid(GUID_VIDEO_SUBGROUP)
_C_GUID_VIDEO_POWERDOWN_TIMEOUT = _uuid_to_guid(GUID_VIDEO_POWERDOWN_TIMEOUT)
_C_GUID_SLEEP_SUBGROUP          = _uuid_to_guid(GUID_SLEEP_SUBGROUP)
_C_GUID_STANDBY_TIMEOUT         = _uuid_to_guid(GUID_STANDBY_TIMEOUT)


# ── DLL 加載 ──────────────────────────────────────────────
_powrprof = ctypes.WinDLL('powrprof')
_kernel32 = ctypes.WinDLL('kernel32')


# ── 函式簽名定義 ──────────────────────────────────────────

# DWORD PowerGetActiveScheme(HKEY, GUID**)
_PowerGetActiveScheme = _powrprof.PowerGetActiveScheme
_PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_PowerGetActiveScheme.restype = ctypes.c_ulong

# DWORD PowerReadACValueIndex(HKEY, const GUID*, const GUID*, const GUID*, LPDWORD)
_PowerReadACValueIndex = _powrprof.PowerReadACValueIndex
_PowerReadACValueIndex.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_ulong),
]
_PowerReadACValueIndex.restype = ctypes.c_ulong

# DWORD PowerReadDCValueIndex(...) — 簽名與 AC 版相同
_PowerReadDCValueIndex = _powrprof.PowerReadDCValueIndex
_PowerReadDCValueIndex.argtypes = _PowerReadACValueIndex.argtypes
_PowerReadDCValueIndex.restype = ctypes.c_ulong

# DWORD PowerWriteACValueIndex(HKEY, const GUID*, const GUID*, const GUID*, DWORD)
_PowerWriteACValueIndex = _powrprof.PowerWriteACValueIndex
_PowerWriteACValueIndex.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(GUID),
    ctypes.c_ulong,
]
_PowerWriteACValueIndex.restype = ctypes.c_ulong

# DWORD PowerWriteDCValueIndex(...) — 簽名與 AC 版相同
_PowerWriteDCValueIndex = _powrprof.PowerWriteDCValueIndex
_PowerWriteDCValueIndex.argtypes = _PowerWriteACValueIndex.argtypes
_PowerWriteDCValueIndex.restype = ctypes.c_ulong

# DWORD PowerSetActiveScheme(HKEY, const GUID*)
_PowerSetActiveScheme = _powrprof.PowerSetActiveScheme
_PowerSetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID)]
_PowerSetActiveScheme.restype = ctypes.c_ulong

# HLOCAL LocalFree(HLOCAL)
_LocalFree = _kernel32.LocalFree
_LocalFree.argtypes = [ctypes.c_void_p]
_LocalFree.restype = ctypes.c_void_p


# ── 輔助 ──────────────────────────────────────────────────

_MAX_DWORD = 0xFFFFFFFF


def _validate_seconds(seconds: int):
    """確保 seconds 在合法範圍內 (0 ~ DWORD_MAX)"""
    if not isinstance(seconds, int) or seconds < 0 or seconds > _MAX_DWORD:
        raise ValueError(
            f'無效的秒數值: {seconds}，必須為 0 ~ {_MAX_DWORD} 之間的整數'
        )


# ── Public API ────────────────────────────────────────────

class PowerManager:
    """電源管理 API 封裝"""

    @staticmethod
    def get_active_scheme() -> UUID:
        """取得當前作用中電源計畫的 GUID"""
        scheme_ptr = ctypes.c_void_p()
        ret = _PowerGetActiveScheme(None, ctypes.byref(scheme_ptr))
        if ret != ERROR_SUCCESS:
            raise OSError(f'PowerGetActiveScheme 失敗，錯誤碼: {ret}')
        if not scheme_ptr.value:
            raise OSError('PowerGetActiveScheme 返回了空指針')
        try:
            buf = (ctypes.c_ubyte * 16).from_address(scheme_ptr.value)
            raw = bytes(buf)
            return UUID(bytes_le=raw)
        finally:
            _LocalFree(scheme_ptr)

    # ── 讀取 ──

    @staticmethod
    def _read_value(
        read_fn, scheme: UUID,
        c_subgroup: GUID, c_setting: GUID,
    ) -> int:
        """通用讀取電源設定值 (秒)

        參數:
            read_fn: _PowerReadACValueIndex 或 _PowerReadDCValueIndex
            scheme: 電源計畫 UUID
            c_subgroup: 快取好的 ctypes subgroup GUID
            c_setting: 快取好的 ctypes setting GUID
        """
        c_scheme = _uuid_to_guid(scheme)
        value = ctypes.c_ulong(0)
        ret = read_fn(None, c_scheme, c_subgroup, c_setting, ctypes.byref(value))
        if ret != ERROR_SUCCESS:
            raise OSError(f'讀取電源設定失敗，錯誤碼: {ret}')
        return value.value

    @staticmethod
    def read_ac_display_timeout(scheme: UUID) -> int:
        """讀取 AC 熄屏超時 (秒)"""
        return PowerManager._read_value(
            _PowerReadACValueIndex, scheme,
            _C_GUID_VIDEO_SUBGROUP, _C_GUID_VIDEO_POWERDOWN_TIMEOUT,
        )

    @staticmethod
    def read_dc_display_timeout(scheme: UUID) -> int:
        """讀取 DC 熄屏超時 (秒)"""
        return PowerManager._read_value(
            _PowerReadDCValueIndex, scheme,
            _C_GUID_VIDEO_SUBGROUP, _C_GUID_VIDEO_POWERDOWN_TIMEOUT,
        )

    @staticmethod
    def read_ac_sleep_timeout(scheme: UUID) -> int:
        """讀取 AC 睡眠超時 (秒)"""
        return PowerManager._read_value(
            _PowerReadACValueIndex, scheme,
            _C_GUID_SLEEP_SUBGROUP, _C_GUID_STANDBY_TIMEOUT,
        )

    @staticmethod
    def read_dc_sleep_timeout(scheme: UUID) -> int:
        """讀取 DC 睡眠超時 (秒)"""
        return PowerManager._read_value(
            _PowerReadDCValueIndex, scheme,
            _C_GUID_SLEEP_SUBGROUP, _C_GUID_STANDBY_TIMEOUT,
        )

    # ── 寫入 ──

    @staticmethod
    def _write_value(
        write_fn, scheme: UUID,
        c_subgroup: GUID, c_setting: GUID,
        seconds: int,
    ):
        """通用寫入電源設定值 (秒)"""
        _validate_seconds(seconds)
        c_scheme = _uuid_to_guid(scheme)
        ret = write_fn(None, c_scheme, c_subgroup, c_setting, ctypes.c_ulong(seconds))
        if ret != ERROR_SUCCESS:
            raise OSError(f'寫入電源設定失敗，錯誤碼: {ret}')

    @staticmethod
    def write_ac_display_timeout(scheme: UUID, seconds: int):
        """寫入 AC 熄屏超時 (秒)"""
        PowerManager._write_value(
            _PowerWriteACValueIndex, scheme,
            _C_GUID_VIDEO_SUBGROUP, _C_GUID_VIDEO_POWERDOWN_TIMEOUT,
            seconds,
        )

    @staticmethod
    def write_dc_display_timeout(scheme: UUID, seconds: int):
        """寫入 DC 熄屏超時 (秒)"""
        PowerManager._write_value(
            _PowerWriteDCValueIndex, scheme,
            _C_GUID_VIDEO_SUBGROUP, _C_GUID_VIDEO_POWERDOWN_TIMEOUT,
            seconds,
        )

    @staticmethod
    def write_ac_sleep_timeout(scheme: UUID, seconds: int):
        """寫入 AC 睡眠超時 (秒)"""
        PowerManager._write_value(
            _PowerWriteACValueIndex, scheme,
            _C_GUID_SLEEP_SUBGROUP, _C_GUID_STANDBY_TIMEOUT,
            seconds,
        )

    @staticmethod
    def write_dc_sleep_timeout(scheme: UUID, seconds: int):
        """寫入 DC 睡眠超時 (秒)"""
        PowerManager._write_value(
            _PowerWriteDCValueIndex, scheme,
            _C_GUID_SLEEP_SUBGROUP, _C_GUID_STANDBY_TIMEOUT,
            seconds,
        )

    # ── 套用 ──

    @staticmethod
    def apply_scheme(scheme: UUID):
        """使電源計畫修改生效 (寫入後必須呼叫)"""
        c_scheme = _uuid_to_guid(scheme)
        ret = _PowerSetActiveScheme(None, c_scheme)
        if ret != ERROR_SUCCESS:
            raise OSError(f'PowerSetActiveScheme 失敗，錯誤碼: {ret}')
