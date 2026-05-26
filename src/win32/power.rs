#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::Mutex;
use thiserror::Error;
use windows::core::GUID;
use windows::Win32::Foundation::*;
use windows::Win32::System::Power::*;
use windows::Win32::System::Registry::HKEY;

// ── GUID Constants ────────────────────────────────────────────
const SUBGROUP_VIDEO: GUID = GUID::from_u128(0x7516b95f_f776_4464_8c53_06167f40cc99);
const SETTING_VIDEO_POWERDOWN: GUID = GUID::from_u128(0x3c0bc021_c8a8_4e07_a973_6b14cbcb2b7e);
const SUBGROUP_SLEEP: GUID = GUID::from_u128(0x238c9fa8_0aad_41ed_83f4_97be242c8f20);
const SETTING_STANDBY: GUID = GUID::from_u128(0x29f6c1db_86da_48c5_9fdb_f2b67b1f44da);

// ── Error ─────────────────────────────────────────────────────
#[derive(Error, Debug)]
pub enum PowerError {
    #[error("Win32 API error: {0}")]
    Win32(#[from] windows::core::Error),
    #[error("Null pointer returned")]
    NullPtr,
}

// ── Trait ─────────────────────────────────────────────────────
pub trait PowerManager: Send + Sync {
    fn get_active_scheme(&self) -> Result<GUID, PowerError>;
    fn read_ac_display_timeout(&self, scheme: &GUID) -> Result<u32, PowerError>;
    fn read_dc_display_timeout(&self, scheme: &GUID) -> Result<u32, PowerError>;
    fn read_ac_sleep_timeout(&self, scheme: &GUID) -> Result<u32, PowerError>;
    fn read_dc_sleep_timeout(&self, scheme: &GUID) -> Result<u32, PowerError>;
    fn write_ac_display_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError>;
    fn write_dc_display_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError>;
    fn write_ac_sleep_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError>;
    fn write_dc_sleep_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError>;
    fn apply_scheme(&self, scheme: &GUID) -> Result<(), PowerError>;
}

// ── Windows Implementation ────────────────────────────────────
pub struct WindowsPowerManager;

impl WindowsPowerManager {
    pub fn new() -> Self { WindowsPowerManager }

    unsafe fn _read(&self, scheme: &GUID, subgroup: &GUID, setting: &GUID, ac: bool) -> Result<u32, PowerError> {
        let mut value: u32 = 0;
        let result: WIN32_ERROR = if ac {
            PowerReadACValueIndex(HKEY::default(), Some(scheme as *const GUID), Some(subgroup as *const GUID), Some(setting as *const GUID), &mut value)
        } else {
            WIN32_ERROR(PowerReadDCValueIndex(HKEY::default(), Some(scheme as *const GUID), Some(subgroup as *const GUID), Some(setting as *const GUID), &mut value))
        };
        if !result.is_ok() {
            return Err(PowerError::Win32(result.to_hresult().into()));
        }
        Ok(value)
    }

    unsafe fn _write(&self, scheme: &GUID, subgroup: &GUID, setting: &GUID, ac: bool, seconds: u32) -> Result<(), PowerError> {
        let result: WIN32_ERROR = if ac {
            PowerWriteACValueIndex(HKEY::default(), scheme as *const GUID, Some(subgroup as *const GUID), Some(setting as *const GUID), seconds)
        } else {
            WIN32_ERROR(PowerWriteDCValueIndex(HKEY::default(), scheme as *const GUID, Some(subgroup as *const GUID), Some(setting as *const GUID), seconds))
        };
        if !result.is_ok() {
            return Err(PowerError::Win32(result.to_hresult().into()));
        }
        Ok(())
    }
}

impl Default for WindowsPowerManager {
    fn default() -> Self { Self::new() }
}

impl PowerManager for WindowsPowerManager {
    fn get_active_scheme(&self) -> Result<GUID, PowerError> {
        unsafe {
            let mut ptr: *mut GUID = std::ptr::null_mut();
            let result = PowerGetActiveScheme(HKEY::default(), &mut ptr);
            if !result.is_ok() {
                return Err(PowerError::Win32(result.to_hresult().into()));
            }
            if ptr.is_null() { return Err(PowerError::NullPtr); }
            let guid = *ptr;
            let _ = LocalFree(HLOCAL(ptr as _));
            Ok(guid)
        }
    }

    fn read_ac_display_timeout(&self, scheme: &GUID) -> Result<u32, PowerError> {
        unsafe { self._read(scheme, &SUBGROUP_VIDEO, &SETTING_VIDEO_POWERDOWN, true) }
    }
    fn read_dc_display_timeout(&self, scheme: &GUID) -> Result<u32, PowerError> {
        unsafe { self._read(scheme, &SUBGROUP_VIDEO, &SETTING_VIDEO_POWERDOWN, false) }
    }
    fn read_ac_sleep_timeout(&self, scheme: &GUID) -> Result<u32, PowerError> {
        unsafe { self._read(scheme, &SUBGROUP_SLEEP, &SETTING_STANDBY, true) }
    }
    fn read_dc_sleep_timeout(&self, scheme: &GUID) -> Result<u32, PowerError> {
        unsafe { self._read(scheme, &SUBGROUP_SLEEP, &SETTING_STANDBY, false) }
    }
    fn write_ac_display_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        unsafe { self._write(scheme, &SUBGROUP_VIDEO, &SETTING_VIDEO_POWERDOWN, true, seconds) }
    }
    fn write_dc_display_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        unsafe { self._write(scheme, &SUBGROUP_VIDEO, &SETTING_VIDEO_POWERDOWN, false, seconds) }
    }
    fn write_ac_sleep_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        unsafe { self._write(scheme, &SUBGROUP_SLEEP, &SETTING_STANDBY, true, seconds) }
    }
    fn write_dc_sleep_timeout(&self, scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        unsafe { self._write(scheme, &SUBGROUP_SLEEP, &SETTING_STANDBY, false, seconds) }
    }
    fn apply_scheme(&self, scheme: &GUID) -> Result<(), PowerError> {
        unsafe {
            let result = PowerSetActiveScheme(HKEY::default(), Some(scheme as *const GUID));
            if !result.is_ok() {
                return Err(PowerError::Win32(result.to_hresult().into()));
            }
            Ok(())
        }
    }
}

// ── Mock Implementation ───────────────────────────────────────
pub struct MockPowerManager {
    values: Mutex<HashMap<String, u32>>,
}

impl MockPowerManager {
    pub fn new() -> Self {
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 300);
        map.insert("dc_display".into(), 300);
        map.insert("ac_sleep".into(), 1800);
        map.insert("dc_sleep".into(), 1800);
        MockPowerManager { values: Mutex::new(map) }
    }
    pub fn from_map(map: HashMap<String, u32>) -> Self {
        MockPowerManager { values: Mutex::new(map) }
    }
}

impl Default for MockPowerManager { fn default() -> Self { Self::new() } }

impl PowerManager for MockPowerManager {
    fn get_active_scheme(&self) -> Result<GUID, PowerError> {
        Ok(GUID::from_u128(0x00000000_0000_0000_0000_000000000001))
    }
    fn read_ac_display_timeout(&self, _scheme: &GUID) -> Result<u32, PowerError> {
        Ok(*self.values.lock().unwrap().get("ac_display").unwrap_or(&300))
    }
    fn read_dc_display_timeout(&self, _scheme: &GUID) -> Result<u32, PowerError> {
        Ok(*self.values.lock().unwrap().get("dc_display").unwrap_or(&300))
    }
    fn read_ac_sleep_timeout(&self, _scheme: &GUID) -> Result<u32, PowerError> {
        Ok(*self.values.lock().unwrap().get("ac_sleep").unwrap_or(&1800))
    }
    fn read_dc_sleep_timeout(&self, _scheme: &GUID) -> Result<u32, PowerError> {
        Ok(*self.values.lock().unwrap().get("dc_sleep").unwrap_or(&1800))
    }
    fn write_ac_display_timeout(&self, _scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        self.values.lock().unwrap().insert("ac_display".into(), seconds); Ok(())
    }
    fn write_dc_display_timeout(&self, _scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        self.values.lock().unwrap().insert("dc_display".into(), seconds); Ok(())
    }
    fn write_ac_sleep_timeout(&self, _scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        self.values.lock().unwrap().insert("ac_sleep".into(), seconds); Ok(())
    }
    fn write_dc_sleep_timeout(&self, _scheme: &GUID, seconds: u32) -> Result<(), PowerError> {
        self.values.lock().unwrap().insert("dc_sleep".into(), seconds); Ok(())
    }
    fn apply_scheme(&self, _scheme: &GUID) -> Result<(), PowerError> { Ok(()) }
}

// ── Tests ─────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mock_default_values() {
        let pm = MockPowerManager::new();
        let scheme = pm.get_active_scheme().unwrap();
        assert_eq!(pm.read_ac_display_timeout(&scheme).unwrap(), 300);
        assert_eq!(pm.read_dc_display_timeout(&scheme).unwrap(), 300);
        assert_eq!(pm.read_ac_sleep_timeout(&scheme).unwrap(), 1800);
        assert_eq!(pm.read_dc_sleep_timeout(&scheme).unwrap(), 1800);
    }

    #[test]
    fn test_mock_write_and_read() {
        let pm = MockPowerManager::new();
        let scheme = pm.get_active_scheme().unwrap();
        pm.write_ac_display_timeout(&scheme, 120).unwrap();
        assert_eq!(pm.read_ac_display_timeout(&scheme).unwrap(), 120);
        pm.apply_scheme(&scheme).unwrap();
    }

    #[test]
    fn test_mock_from_map() {
        let mut map = HashMap::new();
        map.insert("ac_display".into(), 42);
        let pm = MockPowerManager::from_map(map);
        let scheme = pm.get_active_scheme().unwrap();
        assert_eq!(pm.read_ac_display_timeout(&scheme).unwrap(), 42);
        assert_eq!(pm.read_dc_display_timeout(&scheme).unwrap(), 300);
    }
}
