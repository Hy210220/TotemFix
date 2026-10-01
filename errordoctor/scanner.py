# -*- coding: utf-8 -*-
"""日志与崩溃报告扫描器。

数据来源：
  1. PCL 启动器日志：<PCL>/Log1.txt、Log2.txt
  2. 游戏日志：<.minecraft>/logs/latest.log
  3. 崩溃报告：<.minecraft>/crash-reports/*.txt
  4. JVM 崩溃日志：<.minecraft>/hs_err_pid*.log
"""

import os
import re
import time

# ---------------------------------------------------------------- 正则规则

EXC_RE = re.compile(
    r"\b([A-Za-z_$][\w$.]*?(?:Exception|Error|Throwable))(?::|\s|$|\[|\()", re.I)

STACK_RE = re.compile(r"^\s+at\s+[\w.$<>\[\]]+\(")

CRASH_RE = re.compile(r"(游戏崩溃|检测到游戏崩溃|崩溃了|has crashed|Crashed!|"
                      r"An unexpected issue|致命错误)", re.I)

EXIT_RE = re.compile(r"(?:退出代码|exit\s*code)[^\d\-]*(-?\d+)", re.I)

FATAL_RE = re.compile(r"^\s*\[?(FATAL|SEVERE|CRITICAL|ERROR)\]?[:：\s]", re.I)

# 读取文件的最大尾部字节数（避免超大日志拖慢扫描）
MAX_TAIL_BYTES = 3 * 1024 * 1024
MAX_EXCERPT_CHARS = 6000
MAX_ISSUES_PER_FILE = 6
WINDOW_BEFORE = 8
WINDOW_AFTER = 55

SEVERITY_TEXT = {100: "严重", 90: "高", 80: "较高", 70: "中", 60: "中", 50: "低"}

# 用于签名：去掉行首时间戳（[HH:MM:SS] / [HH:MM]），避免日志轮换/时间变化
# 导致同一报错被反复当作“新报错”
_TS_RE = re.compile(r"^\[?\s*\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\]?\s*")


def _norm_sig(title: str) -> str:
    return _TS_RE.sub("", title or "").strip()[:160]


def _read_tail(path: str) -> str:
    """读取文件尾部（崩溃报告等小文件全读）。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= MAX_TAIL_BYTES:
            f.seek(0)
        else:
            f.seek(size - MAX_TAIL_BYTES)
        raw = f.read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _classify(line: str):
    """返回 (kind, severity) 或 None。kind 为中文类别名。"""
    if EXC_RE.search(line):
        return "异常", 90
    if CRASH_RE.search(line):
        return "崩溃", 100
    m = EXIT_RE.search(line)
    if m and int(m.group(1)) != 0:
        return "退出代码", 85
    if FATAL_RE.search(line):
        return "致命错误", 80
    return None


def _scan_log(path: str, label: str):
    """扫描普通日志文件，返回 issue 列表。"""
    try:
        text = _read_tail(path)
    except OSError:
        return []
    lines = text.splitlines()
    if not lines:
        return []

    anchors = []
    for i, line in enumerate(lines):
        if len(line) > 2000:
            continue
        c = _classify(line)
        if c:
            anchors.append((i, line, c))

    issues = []
    seen_sigs = set()
    for i, line, (kind, sev) in anchors:
        start = max(0, i - WINDOW_BEFORE)
        end = min(len(lines), i + WINDOW_AFTER + 1)
        excerpt = "\n".join(lines[start:end]).strip()
        if not excerpt:
            continue
        title = line.strip()
        sig = f"{label}|{kind}|{_norm_sig(title)}"
        if sig in seen_sigs:
            continue  # 同一报错在窗口内重复出现时只保留一条
        seen_sigs.add(sig)
        issues.append({
            "source": label,
            "path": path,
            "kind": kind,
            "severity": sev,
            "severity_text": SEVERITY_TEXT.get(sev, "中"),
            "title": title[:160],
            "excerpt": excerpt[:MAX_EXCERPT_CHARS],
            "line": i + 1,
            "modified": os.path.getmtime(path),
            "sig": sig,
        })
        if len(issues) >= MAX_ISSUES_PER_FILE:
            break
    return issues


def _scan_crash_report(path: str, label: str):
    """整份崩溃报告作为一个 issue。"""
    try:
        text = _read_tail(path)
    except OSError:
        return []
    if not text.strip():
        return []
    lines = text.splitlines()
    title = ""
    for line in lines[:40]:
        s = line.strip()
        if s.startswith("Description:"):
            title = s[len("Description:"):].strip()
            break
        if s.startswith("---- Minecraft Crash Report"):
            continue
        if s and not title and not s.startswith(("//", "Time:", "Thread:")):
            title = s
            break
    if not title:
        title = os.path.basename(path)
    return [{
        "source": label,
        "path": path,
        "kind": "崩溃报告",
        "severity": 100,
        "severity_text": "严重",
        "title": title[:160],
        "excerpt": text[:MAX_EXCERPT_CHARS],
        "line": 1,
        "modified": os.path.getmtime(path),
        "sig": f"{label}|崩溃报告|{title[:160]}",
    }]


def scan(cfg: dict) -> dict:
    """扫描全部数据源，返回 {"issues": [...], "stats": {...}}。"""
    pcl_dir = (cfg.get("pcl_dir") or "").strip()
    mc_dir = (cfg.get("mc_dir") or "").strip()
    issues = []
    sources_checked = []
    errors = []

    # 1) PCL 启动器日志
    if pcl_dir and os.path.isdir(pcl_dir):
        for name in ("Log1.txt", "Log2.txt"):
            p = os.path.join(pcl_dir, name)
            if os.path.isfile(p):
                sources_checked.append(p)
                issues += _scan_log(p, f"PCL {name}")
            elif name == "Log1.txt":
                pass  # Log2 不存在属正常

    # 2) 游戏日志 / 崩溃报告 / JVM 崩溃
    if mc_dir and os.path.isdir(mc_dir):
        latest = os.path.join(mc_dir, "logs", "latest.log")
        if os.path.isfile(latest):
            sources_checked.append(latest)
            issues += _scan_log(latest, "游戏日志 latest.log")

        cr_dir = os.path.join(mc_dir, "crash-reports")
        if os.path.isdir(cr_dir):
            try:
                reports = [os.path.join(cr_dir, f) for f in os.listdir(cr_dir)
                           if f.lower().endswith(".txt")]
                reports.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                for p in reports[:MAX_ISSUES_PER_FILE]:
                    sources_checked.append(p)
                    issues += _scan_crash_report(p, "崩溃报告 " + os.path.basename(p))
            except OSError as e:
                errors.append(str(e))

        try:
            for f in sorted(os.listdir(mc_dir)):
                if f.lower().startswith("hs_err_pid") and f.lower().endswith(".log"):
                    p = os.path.join(mc_dir, f)
                    sources_checked.append(p)
                    issues += _scan_log(p, "JVM 崩溃 " + f)
        except OSError as e:
            errors.append(str(e))

        # 版本隔离：每个 versions/<版本> 下也有独立的日志与崩溃报告
        vers_dir = os.path.join(mc_dir, "versions")
        if os.path.isdir(vers_dir):
            try:
                for vname in sorted(os.listdir(vers_dir))[:20]:
                    vdir = os.path.join(vers_dir, vname)
                    if not os.path.isdir(vdir):
                        continue
                    vlatest = os.path.join(vdir, "logs", "latest.log")
                    if os.path.isfile(vlatest):
                        sources_checked.append(vlatest)
                        issues += _scan_log(vlatest, f"版本 {vname} latest.log")
                    vcr = os.path.join(vdir, "crash-reports")
                    if os.path.isdir(vcr):
                        try:
                            reps = sorted(
                                [os.path.join(vcr, f) for f in os.listdir(vcr)
                                 if f.lower().endswith(".txt")],
                                key=lambda p: os.path.getmtime(p), reverse=True)
                            for p in reps[:2]:
                                sources_checked.append(p)
                                issues += _scan_crash_report(
                                    p, f"版本 {vname} 崩溃报告 " + os.path.basename(p))
                        except OSError:
                            pass
            except OSError as e:
                errors.append(str(e))
    else:
        errors.append("尚未配置 Minecraft 文件夹路径（请到“设置”中填写或自动检测）")

    if not pcl_dir or not os.path.isdir(pcl_dir):
        errors.append("尚未配置 PCL 文件夹路径（请到“设置”中填写或自动检测）")

    issues.sort(key=lambda x: (-x["severity"], -x["modified"]))
    for n, it in enumerate(issues):
        it["id"] = f"i{n + 1}"

    stats = {
        "issue_count": len(issues),
        "sources_checked": len(sources_checked),
        "critical": sum(1 for i in issues if i["severity"] >= 100),
        "high": sum(1 for i in issues if 80 <= i["severity"] < 100),
        "medium": sum(1 for i in issues if i["severity"] < 80),
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"issues": issues, "stats": stats, "errors": errors}
