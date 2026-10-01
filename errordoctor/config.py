# -*- coding: utf-8 -*-
"""配置管理：读写 data/config.json，自动检测 PCL 与 .minecraft 路径。

v2：exe 就放在 PCL 文件夹内，因此 PCL 文件夹 = 程序所在文件夹（最高优先）。
"""

import json
import os
import re
import sys
import subprocess


def app_dir() -> str:
    """程序所在目录（打包成 exe 后为 exe 所在目录，即 PCL 文件夹）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR = os.path.join(app_dir(), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

DEFAULTS = {
    "pcl_dir": "",             # PCL 启动器文件夹（含 PCL.exe），默认=程序所在目录
    "mc_dir": "",              # Minecraft 文件夹（.minecraft）
    "api_key": "",             # DeepSeek API Key（sk-...）
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "autofix": True,           # 检测到报错后自动分析并修复
    "ask_before_fix": True,    # 修改文件前弹窗确认（弹窗里可勾选“下次不再询问”）
    "auto_analyze": True,      # 发现报错后自动调用 AI 分析
    "watch_interval": 3,       # 后台监控扫描间隔（秒）
    "hide_on_close": True,     # 点关闭按钮时最小化到后台继续监控（False 则直接退出）
}


def load() -> dict:
    """读取配置，缺失项用默认值补齐。"""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    clean = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def sanitize_for_web(cfg: dict) -> dict:
    """返回给界面的配置（掩码 API Key）。"""
    out = dict(cfg)
    key = out.get("api_key") or ""
    out["api_key_set"] = bool(key)
    out["api_key"] = ("sk-****" + key[-4:]) if key else ""
    return out


# ---------------------------------------------------------------- 自动检测

def _run(cmd, timeout=6):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _is_pcl_dir(path: str) -> bool:
    if not path:
        return False
    try:
        return os.path.isfile(os.path.join(path, "PCL.exe"))
    except OSError:
        return False


def detect_pcl_dir() -> str:
    """定位 PCL 文件夹。exe 就放在 PCL 文件夹内，故程序所在目录最高优先。"""
    if _is_pcl_dir(app_dir()):
        return os.path.abspath(app_dir())
    candidates = []
    if os.name == "nt":
        proc = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-Process PCL -ErrorAction SilentlyContinue).Path"])
        if proc and "\n" not in proc:
            candidates.append(os.path.dirname(proc))
        candidates += [
            r"D:\PCL", r"C:\PCL", r"D:\Minecraft\PCL", r"C:\Minecraft\PCL",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "PCL"),
            os.path.join(os.environ.get("APPDATA", ""), "PCL"),
        ]
    else:
        candidates += [os.path.expanduser("~/PCL")]
    for c in candidates:
        if _is_pcl_dir(c):
            return os.path.abspath(c)
    # 没找到 PCL.exe 时退而求其次：程序所在目录（可能只是没装 PCL.exe 的检查）
    if os.path.isfile(os.path.join(app_dir(), "Log1.txt")):
        return os.path.abspath(app_dir())
    return ""


_MC_PATH_RE = re.compile(
    r"([A-Za-z]:[\\/][^\r\n\"<>|?*]{0,200}?\.minecraft)|"
    r"((?:/[^\r\n\"<>|?*]{0,200})?/\.minecraft)", re.I)


def _extract_mc_from_text(text: str) -> str:
    """从 PCL 日志 / 配置文本中抓取 .minecraft 文件夹路径。"""
    for m in _MC_PATH_RE.finditer(text or ""):
        p = (m.group(1) or m.group(2)).strip()
        if os.path.isdir(p):
            return os.path.abspath(p)
    return ""


def detect_mc_dir(pcl_dir: str = None) -> str:
    """尽力自动定位 Minecraft 文件夹（.minecraft）。

    顺序：PCL 日志 → PCL Setup.ini → 系统默认位置。
    """
    pcl = pcl_dir or detect_pcl_dir()
    if pcl and os.path.isdir(pcl):
        # 1) PCL 日志里通常记录着 Minecraft 文件夹路径
        for name in ("Log1.txt", "Log2.txt"):
            try:
                with open(os.path.join(pcl, name), "r",
                          encoding="utf-8", errors="replace") as f:
                    text = f.read()
                found = _extract_mc_from_text(text)
                if found:
                    return found
            except OSError:
                continue
        # 2) PCL 配置文件
        for name in ("Setup.ini", "PCL.ini", "config.ini"):
            try:
                with open(os.path.join(pcl, name), "r",
                          encoding="utf-8", errors="replace") as f:
                    text = f.read()
                found = _extract_mc_from_text(text)
                if found:
                    return found
            except OSError:
                continue
    # 3) 系统默认位置
    if os.name == "nt":
        default = os.path.join(os.environ.get("APPDATA", ""), ".minecraft")
        if default and os.path.isdir(default):
            return default
    default = os.path.expanduser("~/.minecraft")
    if os.path.isdir(default):
        return default
    return ""
