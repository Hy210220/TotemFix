# -*- coding: utf-8 -*-
"""内置报错资料库：离线匹配已知报错，给出原因与解决办法（DeepSeek 分析的补充）。

数据文件：errordoctor/kb_data.json（随 exe 打包，零网络依赖）。
条目结构：id / title / patterns（正则片段，忽略大小写搜索）/ category /
cause / solution（步骤列表）/ source（出处链接）。
"""

import json
import os

KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_data.json")


class KnowledgeBase:
    def __init__(self, path: str = None):
        self.path = path or KB_PATH
        self.entries = []
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        raw = data.get("entries") if isinstance(data, dict) else data
        for e in raw or []:
            if isinstance(e, dict) and e.get("title") and e.get("patterns"):
                self.entries.append(e)

    def match(self, text: str, limit: int = 3) -> list:
        """返回与文本匹配的条目列表（按定义顺序，最多 limit 条）。"""
        if not text:
            return []
        low = str(text).lower()
        out = []
        for e in self.entries:
            for pat in e.get("patterns") or []:
                if str(pat).lower() in low:
                    out.append(e)
                    break
            if len(out) >= limit:
                break
        return out


_KB = None


def get_kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


def match(text: str, limit: int = 3) -> list:
    return get_kb().match(text, limit)


def entry_count() -> int:
    return len(get_kb().entries)
