const APP_NAME: &str = "EzWindowsTools";
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// 检查自启动是否已启用
pub fn is_auto_start() -> bool {
    use winreg::enums::*;
    use winreg::RegKey;

    RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey_with_flags(RUN_KEY, KEY_READ)
        .ok()
        .and_then(|key| key.get_value::<String, _>(APP_NAME).ok())
        .is_some()
}

/// 设置自启动
pub fn set_auto_start(enabled: bool) -> Result<(), Box<dyn std::error::Error>> {
    use winreg::enums::*;
    use winreg::RegKey;

    let key = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey_with_flags(RUN_KEY, KEY_SET_VALUE)?;

    if enabled {
        // 获取当前 exe 路径
        let exe_path = std::env::current_exe()?;
        // 用引号包裹以支持路径中的空格
        let value = format!("\"{}\"", exe_path.to_string_lossy());
        key.set_value(APP_NAME, &value)?;
    } else {
        key.delete_value(APP_NAME)?;
    }

    Ok(())
}
