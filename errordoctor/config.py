# -*- coding: utf-8 -*-
"""配置管理：读写 data/config.json，自动检测 PCL 与 .minecraft 路径。

v2.1 检测增强：
  * PCL 目录识别兼容真实 exe 名（Plain Craft Launcher 2.exe 等），
    并支持通过运行中的 PCL2 进程/窗口自动定位；
  * .minecraft 多候选检测：PCL 日志 → PCL 的 Setup.ini（当前选择的
    游戏文件夹）→ 注册表 LaunchFolders（用户设置的全部游戏文件夹）
    → 系统默认位置，另提供全盘扫描列出所有 .minecraft 供用户选择。
"""

import json
import os
import re
import string
import subprocess
import sys
import time

# ---------------------------------------------------------------- 常量

def app_dir() -> str:
    """程序所在目录（打包成 exe 后为 exe 所在目录，即 PCL 文件夹）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR = os.path.join(app_dir(), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

DEFAULTS = {
    "pcl_dir": "",             # PCL 启动器文件夹（默认=程序所在目录）
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

# PCL2 的真实 exe 名不止一个（官方新版为 Plain Craft Launcher 2.exe）
PCL_EXE_NAMES = (
    "pcl.exe",
    "plain craft launcher 2.exe",
    "plaincraftlauncher2.exe",
    "plaincraftlauncher.exe",
    "pcl2.exe",
)

PCL_PROCESS_NAMES = ("Plain Craft Launcher 2", "PlainCraftLauncher2", "PCL")


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


# ---------------------------------------------------------------- 基础工具

def _run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _is_pcl_dir(path: str) -> bool:
    """判定某目录是否为 PCL 文件夹：含 PCL 主程序 或 含 PCL 日志（忽略大小写）。"""
    if not path:
        return False
    try:
        names = {n.lower() for n in os.listdir(path)}
    except OSError:
        return False
    for name in PCL_EXE_NAMES:
        if name in names:
            return True
    return "log1.txt" in names


# ---------------------------------------------------------------- PCL 进程/窗口检测

def _looks_like_pcl_exe(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    base = os.path.basename(path).lower()
    if base in PCL_EXE_NAMES:
        return True
    return _is_pcl_dir(os.path.dirname(path))


def detect_pcl_process() -> str:
    """检测正在运行的 PCL2（优先按主窗口标题识别，其次按进程名）。

    返回其 exe 完整路径；未运行或非 Windows 返回空字符串。
    """
    if os.name != "nt":
        return ""
    # 1) 按主窗口标题识别（“识别运行窗口，随后查找位置”）
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "$p = Get-Process | Where-Object { $_.MainWindowTitle -and "
          "($_.MainWindowTitle -like '*Plain Craft Launcher*' -or "
          " $_.MainWindowTitle -like '*PCL*') } | Select-Object -First 1; "
          "if ($p) { $p.Path }")
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
    if out and "\n" not in out and _looks_like_pcl_exe(out):
        return out
    # 2) 按进程名回退
    for name in PCL_PROCESS_NAMES:
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-Process -Name '" + name +
                    "' -ErrorAction SilentlyContinue | "
                    "Select-Object -First 1).Path"], timeout=10)
        if out and "\n" not in out and _looks_like_pcl_exe(out):
            return out
    return ""


def detect_pcl_dir() -> str:
    """定位 PCL 文件夹。优先级：程序所在目录 → 运行中的 PCL2 进程 → 常见位置。"""
    if _is_pcl_dir(app_dir()):
        return os.path.abspath(app_dir())
    candidates = []
    if os.name == "nt":
        proc = detect_pcl_process()
        if proc:
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
    if os.path.isfile(os.path.join(app_dir(), "Log1.txt")):
        return os.path.abspath(app_dir())
    return ""


# ---------------------------------------------------------------- PCL 配置解析

def _read_setup_ini(pcl_dir: str) -> dict:
    r"""解析 <PCL>\Setup.ini（PCL 官方格式：每行 Key:Value）。"""
    p = os.path.join(pcl_dir, "Setup.ini")
    out = {}
    if not os.path.isfile(p):
        return out
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                out[k.strip()] = v
    except OSError:
        pass
    return out


def _resolve_pcl_rel(value: str, pcl_dir: str) -> str:
    """还原 PCL 存储的相对路径：值以 $ 开头表示相对 PCL 文件夹。"""
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith("$"):
        v = os.path.join(pcl_dir, v[1:].lstrip("\\/"))
    return v


def _read_registry_launch_folders() -> list:
    """从注册表读取 PCL 的 LaunchFolders 值。

    官方版与开源版的注册表键名不同（HKCU\\Software\\<RegFolder>），
    故扫描 HKCU\\Software 下所有含 LaunchFolders 值的子键。
    """
    if os.name != "nt":
        return []
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "$out=@(); "
          "Get-ChildItem 'HKCU:\\Software' -ErrorAction SilentlyContinue | "
          "ForEach-Object { "
          "  $v=(Get-ItemProperty $_.PSPath -Name LaunchFolders "
          "-ErrorAction SilentlyContinue).LaunchFolders; "
          "  if ($v) { $out += [string]$v } }; "
          "$out -join [char]10")
    text = _run(["powershell", "-NoProfile", "-Command", ps], timeout=12)
    return [l for l in text.splitlines() if l.strip()]


def read_pcl_game_dirs(pcl_dir: str) -> list:
    """读取用户在 PCL 里设置的游戏文件夹：
    1. Setup.ini 的 LaunchFolderSelect（当前选择，最高优先）；
    2. 注册表 LaunchFolders 列表（格式：显示名>路径，以 | 分隔）。
    """
    out = []
    setup = _read_setup_ini(pcl_dir)
    sel = setup.get("LaunchFolderSelect", "").strip()
    if sel:
        resolved = _resolve_pcl_rel(sel, pcl_dir)
        if resolved:
            out.append(resolved)
    for raw in _read_registry_launch_folders():
        for entry in raw.split("|"):
            entry = entry.strip()
            if not entry:
                continue
            path = entry.split(">", 1)[-1].strip()
            path = _resolve_pcl_rel(path, pcl_dir)
            if path:
                out.append(path)
    return out


# ---------------------------------------------------------------- .minecraft 检测

_MC_PATH_RE = re.compile(
    r"([A-Za-z]:[\\/][^\r\n\"<>|?*]{0,200}?\.minecraft)|"
    r"((?:/[^\r\n\"<>|?*]{0,200})?/\.minecraft)", re.I)


def _extract_mc_from_text_all(text: str) -> list:
    """从 PCL 日志/配置文本中抓取全部 .minecraft 文件夹路径。"""
    out = []
    for m in _MC_PATH_RE.finditer(text or ""):
        p = (m.group(1) or m.group(2)).strip()
        if p and os.path.isdir(p):
            out.append(os.path.abspath(p))
    return out


def detect_mc_dirs(pcl_dir: str = None) -> list:
    """返回候选 Minecraft 文件夹列表（按优先级排序、去重、只保留存在的目录）。

    优先级：PCL 日志中实际使用过的 → PCL 设置（当前选择 + 全部文件夹列表）
    → 系统默认位置。
    """
    cands = []
    pcl = pcl_dir or detect_pcl_dir()
    if pcl and os.path.isdir(pcl):
        for name in ("Log1.txt", "Log2.txt"):
            try:
                with open(os.path.join(pcl, name), "r",
                          encoding="utf-8", errors="replace") as f:
                    text = f.read()
                cands += _extract_mc_from_text_all(text)
            except OSError:
                continue
        cands += read_pcl_game_dirs(pcl)
    cands.append(os.path.join(os.environ.get("APPDATA", ""), ".minecraft"))
    cands.append(os.path.expanduser("~/.minecraft"))

    seen, out = set(), []
    for c in cands:
        c = (c or "").strip().rstrip("\\/")
        if not c:
            continue
        key = os.path.normcase(os.path.abspath(c))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(c):
            out.append(os.path.abspath(c))
    return out


def detect_mc_dir(pcl_dir: str = None) -> str:
    """返回最优先的候选 .minecraft 目录（没有则空串）。"""
    dirs = detect_mc_dirs(pcl_dir)
    return dirs[0] if dirs else ""


# ---------------------------------------------------------------- 全盘扫描

_SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "node_modules",
    ".git", "bin", "obj", "perflogs", "msocache", "config.msi",
    "temp", "tmp", "cache", "winsxs", "sysnative", "drivers", "system32",
    "appdata",  # 默认位置 %APPDATA%\.minecraft 已单独覆盖，跳过提速
    ".minecraft",  # 已收集，无需下钻
}


def scan_all_minecraft_dirs(max_depth: int = 5, timeout: float = 60,
                            roots: list = None) -> list:
    """全盘扫描所有 .minecraft 文件夹（带剪枝与超时保护）。

    roots 参数用于测试；Windows 默认枚举全部盘符。
    """
    results = set()
    if roots is None:
        if os.name == "nt":
            roots = [f"{d}:\\" for d in string.ascii_uppercase
                     if os.path.isdir(f"{d}:\\")]
        else:
            roots = [os.path.expanduser("~"), "/"]
    deadline = time.time() + timeout
    for root in roots:
        _walk_for_minecraft(root, results, max_depth, deadline)
        if time.time() > deadline:
            break
    return sorted(p for p in results if os.path.isdir(p))


def _walk_for_minecraft(root: str, results: set, max_depth: int, deadline: float):
    from collections import deque
    queue = deque([(root, 0)])
    while queue:
        if time.time() > deadline:
            return
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue  # 不再向更深层下钻（根目录深度为 0）
        try:
            entries = list(os.scandir(cur))
        except (OSError, PermissionError):
            continue
        for e in entries:
            if time.time() > deadline:
                return
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = e.name.lower()
            if name == ".minecraft":
                results.add(os.path.abspath(e.path))
                continue
            if name in _SKIP_DIRS or name.startswith("."):
                continue
            queue.append((e.path, depth + 1))
