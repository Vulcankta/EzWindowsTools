"""
JSONC 工具 — 移除 JSON 注释（// 和 /* */）
支援字串內保留原文
"""


def strip_jsonc(text: str) -> str:
    """移除 JSONC 注释，支援字串內保留原文"""
    result: list[str] = []
    in_str = False
    str_char: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            result.append(ch)
            if ch == '\\':
                i += 1
                if i < len(text):
                    result.append(text[i])
            elif ch == str_char:
                in_str = False
                str_char = None
        else:
            if ch == '"':
                in_str = True
                str_char = ch
                result.append(ch)
            elif ch == '/' and i + 1 < len(text):
                if text[i + 1] == '/':
                    i += 2
                    while i < len(text) and text[i] != '\n':
                        i += 1
                    continue
                elif text[i + 1] == '*':
                    i += 2
                    while i + 1 < len(text) and not (text[i] == '*' and text[i + 1] == '/'):
                        i += 1
                    i += 2
                    continue
                else:
                    result.append(ch)
            else:
                result.append(ch)
        i += 1
    return ''.join(result)
