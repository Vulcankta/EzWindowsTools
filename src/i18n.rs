use std::collections::HashMap;
use std::path::Path;

/// 剥离 JSONC 注释（// 和 /* */）
pub fn strip_jsonc(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut in_string = false;
    let mut chars = input.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '"' {
            // 检查前一个字符是否为反斜杠（忽略偶数个反斜杠）
            let is_escaped = out.ends_with('\\') && !out.ends_with("\\\\");
            if !is_escaped {
                in_string = !in_string;
            }
            out.push(c);
        } else if !in_string && c == '/' && chars.peek() == Some(&'/') {
            // 行注释 // → 跳过到行尾
            chars.next();
            while let Some(&ch) = chars.peek() {
                if ch == '\n' {
                    break;
                }
                chars.next();
            }
        } else if !in_string && c == '/' && chars.peek() == Some(&'*') {
            // 块注释 /* */ → 跳过到 */
            chars.next();
            while let Some(ch) = chars.next() {
                if ch == '*' && chars.peek() == Some(&'/') {
                    chars.next();
                    break;
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// 语言信息
pub struct LocaleInfo {
    pub code: String,
    pub name: String,
}

/// 简易 i18n 引擎
pub struct I18n {
    fallback: HashMap<String, String>,
    current: HashMap<String, String>,
    pub current_lang: String,
    pub available: Vec<LocaleInfo>,
}

impl I18n {
    /// 从 locales 目录加载，initial_lang 指定初始语言
    pub fn load(locales_dir: &Path, initial_lang: &str) -> Self {
        let mut fallback = HashMap::new();
        let mut current = HashMap::new();
        let mut available = Vec::new();
        let current_lang = initial_lang.to_string();

        if let Ok(entries) = std::fs::read_dir(locales_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("jsonc") {
                    continue;
                }
                let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("en");
                let code = stem.to_string();

                // 读取文件
                if let Ok(content) = std::fs::read_to_string(&path) {
                    let cleaned = strip_jsonc(&content);
                    if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(&cleaned) {
                        available.push(LocaleInfo {
                            code: code.clone(),
                            name: map
                                .get("locale.name")
                                .cloned()
                                .unwrap_or_else(|| code.clone()),
                        });
                        if code == "en" {
                            fallback = map.clone();
                        }
                        if code == current_lang {
                            current = map;
                        }
                    }
                }
            }
        }

        // 如果 current 是空的（例如 en 本身就是当前语言），用 fallback
        if current.is_empty() {
            current = fallback.clone();
        }

        I18n { fallback, current, current_lang, available }
    }

    /// 切换语言
    pub fn set_language(&mut self, code: &str, locales_dir: &Path) {
        if code == self.current_lang {
            return;
        }
        let path = locales_dir.join(format!("{}.jsonc", code));
        if let Ok(content) = std::fs::read_to_string(&path) {
            let cleaned = strip_jsonc(&content);
            if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(&cleaned) {
                self.current = map;
                self.current_lang = code.to_string();
                return;
            }
        }
        // 不存在的语言 → 回退英文
        self.current = self.fallback.clone();
        self.current_lang = "en".to_string();
    }

    /// 获取翻译，三级回退：current → fallback → key
    pub fn get(&self, key: &str) -> String {
        self.current
            .get(key)
            .or_else(|| self.fallback.get(key))
            .cloned()
            .unwrap_or_else(|| key.to_string())
    }

    /// 带格式化参数的翻译
    pub fn get_fmt(&self, key: &str, args: &[(&str, &str)]) -> String {
        let mut s = self.get(key);
        for (k, v) in args {
            s = s.replace(&format!("{{{}}}", k), v);
        }
        s
    }
}
