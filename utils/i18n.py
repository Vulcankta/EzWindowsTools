"""
国际化引擎 — JSONC 语言文件加载、回退链、全局 _() 函数

回退链: 当前语言 → en（基准）→ 返回 key 原文
"""

import json
import logging
from pathlib import Path
from typing import Optional

from utils.jsonc import strip_jsonc


class I18n:
    """国际化引擎"""

    def __init__(self, locales_dir: Path) -> None:
        self._locales_dir = Path(locales_dir)
        self._fallback: dict[str, str] = {}      # en 基准
        self._current: dict[str, str] = {}       # 当前语言（已合并）
        self._current_lang: str = 'en'
        self._available: list[dict] = []         # 可用语言列表

        self._load_fallback()
        self.set_language('en')

    # ── 加载 ────────────────────────────────────────────────

    def _load_file(self, path: Path) -> dict[str, str]:
        """加载单个语言文件，返回扁平 key→value 字典"""
        try:
            raw = path.read_text(encoding='utf-8')
            cleaned = strip_jsonc(raw)
            data = json.loads(cleaned)
            # 过滤掉元数据键（以 $ 开头的）
            return {k: v for k, v in data.items() if isinstance(v, str) and not k.startswith('$')}
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logging.warning(f'加载语言文件失败 {path}: {e}')
            return {}

    def _load_fallback(self) -> None:
        """加载 en 作为基准"""
        en_path = self._locales_dir / 'en.jsonc'
        self._fallback = self._load_file(en_path)
        logging.info(f'基准语言加载: {len(self._fallback)} keys')

    def _scan(self) -> list[dict]:
        """扫描 locales/ 目录，返回可用语言列表 [{code, name, path}, ...]

        结果缓存在 _scan_cache 中，set_language 时清空。
        """
        if hasattr(self, '_scan_cache'):
            return self._scan_cache
        available = []
        if self._locales_dir.is_dir():
            for f in sorted(self._locales_dir.iterdir()):
                if f.suffix in ('.jsonc', '.json') and f.stem:
                    try:
                        raw = f.read_text(encoding='utf-8')
                        cleaned = strip_jsonc(raw)
                        meta = json.loads(cleaned)
                        code = meta.get('$code', f.stem)
                        name = meta.get('$name', f.stem)
                        available.append({
                            'code': code,
                            'name': name,
                            'path': f,
                        })
                    except Exception as e:
                        logging.debug(f'扫描语言文件跳过 {f.name}: {e}')
        self._scan_cache = available
        return available

    # ── 语言切换 ────────────────────────────────────────────

    def set_language(self, code_or_path: str) -> None:
        """切换语言

        参数:
            code_or_path:
                - 内置语言 code: 'en', 'zh-CN', 'zh-TW'
                - 自定义文件绝对路径（以 .jsonc 或 .json 结尾）
        """
        # 清空扫描缓存
        if hasattr(self, '_scan_cache'):
            del self._scan_cache
        path = Path(code_or_path)

        if path.is_absolute() and path.suffix in ('.jsonc', '.json'):
            # 自定义语言文件
            data = self._load_file(path)
            self._current = {**self._fallback, **data}
            self._current_lang = path.stem
            logging.info(f'已加载自定义语言: {path}')
        else:
            # 内置语言
            lang_file = self._locales_dir / f'{code_or_path}.jsonc'
            if not lang_file.exists():
                lang_file = self._locales_dir / f'{code_or_path}.json'
            if lang_file.exists():
                data = self._load_file(lang_file)
                self._current = {**self._fallback, **data}
                self._current_lang = code_or_path
                logging.info(f'已切换语言: {code_or_path}')
            else:
                logging.warning(f'语言 "{code_or_path}" 未找到，使用英文')
                self._current = dict(self._fallback)
                self._current_lang = 'en'

    # ── 查询 ────────────────────────────────────────────────

    def get(self, key: str, **fmt) -> str:
        """获取翻译文本

        回退链: 当前语言 → en → key 本身
        支持格式: _('hello {name}', name='World') → 'Hello World'
        """
        text = self._current.get(key) or self._fallback.get(key) or key
        if fmt:
            try:
                return text.format(**fmt)
            except KeyError:
                return text
        return text

    @property
    def current_language(self) -> str:
        return self._current_lang

    @property
    def available_languages(self) -> list[dict]:
        """返回可用语言列表（惰性扫描，set_language 时刷新缓存）"""
        builtin = self._scan()
        return builtin


# ── 全局便捷函数 ──────────────────────────────────────────

_current_i18n: Optional[I18n] = None


def init(locales_dir: Path) -> I18n:
    """初始化全局 i18n 实例"""
    global _current_i18n
    _current_i18n = I18n(locales_dir)
    return _current_i18n


def set_global(i18n: I18n) -> None:
    """设置全局 i18n 实例"""
    global _current_i18n
    _current_i18n = i18n


def _(key: str, **fmt) -> str:
    """全局翻译函数（类似 gettext 的 _ 约定）

    未初始化时返回 key 原文（避免 python -O 跳过 assert 导致崩溃）
    """
    if _current_i18n is None:
        return key
    return _current_i18n.get(key, **fmt)
