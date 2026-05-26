//! Windows 显示设备管理 API 封装
//!
//! 提供:
//! - 枚举已连接的显示器
//! - 获取/设置指定显示器的刷新率
//! - 枚举指定显示器支持的刷新率列表

use std::ffi::OsStr;
use std::mem::size_of;
use std::os::windows::ffi::OsStrExt;

use thiserror::Error;
use windows::core::PCWSTR;
use windows::Win32::Foundation::{FALSE, HWND};
use windows::Win32::Graphics::Gdi::{
    ChangeDisplaySettingsExW, DEVMODEW, EnumDisplayDevicesW, EnumDisplaySettingsExW,
    CDS_TEST, CDS_TYPE, DEVMODE_FIELD_FLAGS, DISP_CHANGE_BADMODE, DISP_CHANGE_FAILED,
    DISP_CHANGE_RESTART, DISP_CHANGE_SUCCESSFUL, DISPLAY_DEVICEW, DM_BITSPERPEL,
    DM_DISPLAYFLAGS, DM_DISPLAYFREQUENCY, DM_PELSHEIGHT, DM_PELSWIDTH,
    ENUM_CURRENT_SETTINGS, ENUM_DISPLAY_SETTINGS_FLAGS, ENUM_DISPLAY_SETTINGS_MODE,
    EDS_RAWMODE, DISPLAY_DEVICE_ATTACHED_TO_DESKTOP, DISPLAY_DEVICE_MIRRORING_DRIVER,
};

// ── 错误类型 ─────────────────────────────────────────────────

#[derive(Error, Debug)]
pub enum DisplayError {
    #[error("Win32 API error: {0}")]
    Win32(#[from] windows::core::Error),
    #[error("Failed: {0}")]
    Failed(String),
}

// ── 数据结构 ─────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct DisplayInfo {
    pub name: String,            // e.g. "\\.\DISPLAY1"
    pub friendly_name: String,   // e.g. "DELL S2721QS"
    pub current_refresh_rate: u32,
    pub current_width: u32,
    pub current_height: u32,
}

// ── 抽象 trait ──────────────────────────────────────────────

pub trait DisplayManager: Send + Sync {
    fn get_connected_displays(&self) -> Result<Vec<DisplayInfo>, DisplayError>;
    fn get_supported_refresh_rates(
        &self,
        name: &str,
        width: u32,
        height: u32,
    ) -> Result<Vec<u32>, DisplayError>;
    fn set_refresh_rate(&self, name: &str, target_hz: u32) -> Result<(bool, String), DisplayError>;
}

// ── 辅助函数 ────────────────────────────────────────────────

/// 将 Rust `&str` 转换为以 null 结尾的 UTF-16 `Vec<u16>`。
fn to_wstr(s: &str) -> Vec<u16> {
    OsStr::new(s).encode_wide().chain(std::iter::once(0)).collect()
}

/// 将 Windows 风格的以 null 结尾的 `[u16]` 切片转换为 Rust `String`。
fn wide_to_string(wchars: &[u16]) -> String {
    let len = wchars.iter().position(|&c| c == 0).unwrap_or(wchars.len());
    String::from_utf16_lossy(&wchars[..len])
}

/// 从 `DISPLAY_DEVICEW.DeviceName` 中提取设备名称，去除尾部反斜杠。
fn device_name_from_dd(dd: &DISPLAY_DEVICEW) -> String {
    let raw = wide_to_string(&dd.DeviceName);
    raw.trim_end_matches('\\').to_string()
}

// ── Windows 实现 ────────────────────────────────────────────

pub struct WindowsDisplayManager;

impl WindowsDisplayManager {
    pub fn new() -> Self {
        Self
    }
}

impl Default for WindowsDisplayManager {
    fn default() -> Self {
        Self::new()
    }
}

impl DisplayManager for WindowsDisplayManager {
    /// 枚举当前已连接且非镜像的所有显示器。
    fn get_connected_displays(&self) -> Result<Vec<DisplayInfo>, DisplayError> {
        let mut displays: Vec<DisplayInfo> = Vec::new();

        let mut i = 0u32;
        loop {
            let mut dd = DISPLAY_DEVICEW::default();
            dd.cb = size_of::<DISPLAY_DEVICEW>() as u32;

            let found = unsafe { EnumDisplayDevicesW(PCWSTR::null(), i, &mut dd, 0) };
            if found == FALSE {
                break;
            }

            let is_attached =
                (dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) != 0;
            let is_mirroring =
                (dd.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER) != 0;

            if is_attached && !is_mirroring {
                let name = device_name_from_dd(&dd);
                let name_wide = to_wstr(&name);

                let mut dm = DEVMODEW::default();
                dm.dmSize = size_of::<DEVMODEW>() as u16;

                let got_settings = unsafe {
                    EnumDisplaySettingsExW(
                        PCWSTR::from_raw(name_wide.as_ptr()),
                        ENUM_CURRENT_SETTINGS,
                        &mut dm,
                        ENUM_DISPLAY_SETTINGS_FLAGS(0),
                    )
                };
                if got_settings != FALSE {
                    displays.push(DisplayInfo {
                        name,
                        friendly_name: wide_to_string(&dd.DeviceString),
                        current_refresh_rate: dm.dmDisplayFrequency,
                        current_width: dm.dmPelsWidth,
                        current_height: dm.dmPelsHeight,
                    });
                }
            }

            i += 1;
        }

        Ok(displays)
    }

    /// 枚举指定显示器支持的刷新率。
    ///
    /// 如果 `width` 和 `height` 均为 0，返回所有分辨率下的全部刷新率（去重升序）；
    /// 否则只返回该分辨率下的刷新率。
    fn get_supported_refresh_rates(
        &self,
        name: &str,
        width: u32,
        height: u32,
    ) -> Result<Vec<u32>, DisplayError> {
        let name_wide = to_wstr(name);
        let mut rates: Vec<u32> = Vec::new();
        let mut mode_index = 0u32;

        loop {
            let mut dm = DEVMODEW::default();
            dm.dmSize = size_of::<DEVMODEW>() as u16;

            let found = unsafe {
                EnumDisplaySettingsExW(
                    PCWSTR::from_raw(name_wide.as_ptr()),
                    ENUM_DISPLAY_SETTINGS_MODE(mode_index),
                    &mut dm,
                    EDS_RAWMODE,
                )
            };
            if found == FALSE {
                break;
            }

            if dm.dmDisplayFrequency > 0 && dm.dmPelsWidth > 0 {
                let freq = dm.dmDisplayFrequency;
                if width > 0 && height > 0 {
                    if dm.dmPelsWidth == width && dm.dmPelsHeight == height {
                        if !rates.contains(&freq) {
                            rates.push(freq);
                        }
                    }
                } else {
                    if !rates.contains(&freq) {
                        rates.push(freq);
                    }
                }
            }

            mode_index += 1;
        }

        rates.sort_unstable();
        Ok(rates)
    }

    /// 设置指定显示器的刷新率。
    ///
    /// 先测试模式是否可用，再实际应用。
    fn set_refresh_rate(&self, name: &str, target_hz: u32) -> Result<(bool, String), DisplayError> {
        let name_wide = to_wstr(name);

        // 读取当前设置
        let mut dm = DEVMODEW::default();
        dm.dmSize = size_of::<DEVMODEW>() as u16;

        let got_settings = unsafe {
            EnumDisplaySettingsExW(
                PCWSTR::from_raw(name_wide.as_ptr()),
                ENUM_CURRENT_SETTINGS,
                &mut dm,
                ENUM_DISPLAY_SETTINGS_FLAGS(0),
            )
        };
        if got_settings == FALSE {
            return Ok((false, "无法读取当前显示器设置".to_string()));
        }

        // 修改刷新率
        dm.dmFields = DEVMODE_FIELD_FLAGS(
            DM_BITSPERPEL.0 | DM_PELSWIDTH.0 | DM_PELSHEIGHT.0 | DM_DISPLAYFLAGS.0 | DM_DISPLAYFREQUENCY.0,
        );
        dm.dmDisplayFrequency = target_hz;

        // 先测试
        let result = unsafe {
            ChangeDisplaySettingsExW(
                PCWSTR::from_raw(name_wide.as_ptr()),
                Some(&dm as *const DEVMODEW),
                HWND::default(),
                CDS_TEST,
                None,
            )
        };
        if result == DISP_CHANGE_FAILED {
            return Ok((false, format!("测试失败：不支持 {}Hz", target_hz)));
        }
        if result == DISP_CHANGE_BADMODE {
            return Ok((false, format!("不支持的模式：{}Hz", target_hz)));
        }
        if result != DISP_CHANGE_SUCCESSFUL && result != DISP_CHANGE_RESTART {
            return Ok((false, format!("测试返回异常代码: {}", result.0)));
        }

        // 应用
        let result = unsafe {
            ChangeDisplaySettingsExW(
                PCWSTR::from_raw(name_wide.as_ptr()),
                Some(&dm as *const DEVMODEW),
                HWND::default(),
                CDS_TYPE(0),
                None,
            )
        };
        match result {
            DISP_CHANGE_SUCCESSFUL => {
                Ok((true, format!("已设为 {}Hz", target_hz)))
            }
            DISP_CHANGE_RESTART => {
                Ok((true, format!("需要重启以应用 {}Hz", target_hz)))
            }
            _ => {
                Ok((false, format!("应用失败，代码: {}", result.0)))
            }
        }
    }
}

// ── Mock 实现（测试用） ────────────────────────────────────

pub struct MockDisplayManager {
    pub displays: Vec<DisplayInfo>,
    pub supported_rates: Vec<u32>,
}

impl DisplayManager for MockDisplayManager {
    fn get_connected_displays(&self) -> Result<Vec<DisplayInfo>, DisplayError> {
        Ok(self.displays.clone())
    }

    fn get_supported_refresh_rates(
        &self,
        _name: &str,
        _width: u32,
        _height: u32,
    ) -> Result<Vec<u32>, DisplayError> {
        Ok(self.supported_rates.clone())
    }

    fn set_refresh_rate(&self, _name: &str, target_hz: u32) -> Result<(bool, String), DisplayError> {
        Ok((true, format!("Set to {}Hz", target_hz)))
    }
}

// ── 单元测试 ──────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mock_get_connected_displays() {
        let mock = MockDisplayManager {
            displays: vec![DisplayInfo {
                name: "\\\\.\\DISPLAY1".to_string(),
                friendly_name: "Mock Monitor".to_string(),
                current_refresh_rate: 60,
                current_width: 1920,
                current_height: 1080,
            }],
            supported_rates: vec![60, 120],
        };

        let displays = mock.get_connected_displays().unwrap();
        assert_eq!(displays.len(), 1);
        assert_eq!(displays[0].name, "\\\\.\\DISPLAY1");
        assert_eq!(displays[0].current_refresh_rate, 60);
    }

    #[test]
    fn test_mock_get_supported_refresh_rates() {
        let mock = MockDisplayManager {
            displays: vec![],
            supported_rates: vec![60, 120, 144],
        };

        let rates = mock
            .get_supported_refresh_rates("\\\\.\\DISPLAY1", 1920, 1080)
            .unwrap();
        assert_eq!(rates, vec![60, 120, 144]);
    }

    #[test]
    fn test_mock_set_refresh_rate() {
        let mock = MockDisplayManager {
            displays: vec![],
            supported_rates: vec![],
        };

        let (success, msg) = mock.set_refresh_rate("\\\\.\\DISPLAY1", 144).unwrap();
        assert!(success);
        assert_eq!(msg, "Set to 144Hz");
    }

    #[test]
    fn test_wide_to_string() {
        let input = [72u16, 101, 108, 108, 111, 0];
        assert_eq!(wide_to_string(&input), "Hello");
    }

    #[test]
    fn test_wide_to_string_no_null() {
        let input = [72u16, 101, 108, 108, 111];
        assert_eq!(wide_to_string(&input), "Hello");
    }

    #[test]
    fn test_to_wstr() {
        let result = to_wstr("ABC");
        assert_eq!(result, vec![65u16, 66, 67, 0]);
    }

    #[test]
    fn test_device_name_from_dd_removes_trailing_backslash() {
        let mut dd = DISPLAY_DEVICEW::default();
        let raw: Vec<u16> = "\\\\.\\DISPLAY1\\"
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        for (i, &v) in raw.iter().enumerate() {
            dd.DeviceName[i] = v;
        }
        assert_eq!(device_name_from_dd(&dd), "\\\\.\\DISPLAY1");
    }
}
