# -*- coding: utf-8 -*-
"""本地历史记录：data/history.json，最多保留 200 条。"""

import json
import os
import time

from . import config

MAX_RECORDS = 200


def _load() -> list:
    try:
        with open(config.HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add(kind: str, summary: str, detail: str = "") -> None:
    """kind: scan / analyze / fix / restore / config"""
    records = _load()
    records.insert(0, {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "summary": summary[:500],
        "detail": (detail or "")[:4000],
    })
    del records[MAX_RECORDS:]
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def list_records(limit: int = 50) -> list:
    return _load()[:limit]


def clear() -> None:
    try:
        if os.path.isfile(config.HISTORY_PATH):
            os.remove(config.HISTORY_PATH)
    except OSError:
        pass
