# -*- coding: utf-8 -*-
"""后台监控器（轮询实现，零依赖）。

  * Watcher：监控 PCL 日志、游戏日志、崩溃报告目录、JVM 崩溃日志，
    文件新增/修改/大小变化 → 触发 on_change（内置去抖）。
  * PclProcessWatcher：监控 PCL2 进程/窗口，一旦检测到用户打开了
    PCL2 即回调其 exe 路径（用于自动定位 PCL 文件夹）。
"""

import os
import threading
import time

from . import config


class Watcher(threading.Thread):
    def __init__(self, cfg: dict, on_change, interval: float = None,
                 debounce: float = 0.5):
        super().__init__(daemon=True, name="watcher")
        self.cfg = cfg
        self.on_change = on_change          # 回调：文件有变化
        self.interval = float(interval if interval is not None
                              else cfg.get("watch_interval", 3))
        self.debounce = debounce
        self._stop = threading.Event()

    # ------------------------------------------------------------ 快照

    def _watched(self):
        """返回要监控的 (文件路径) 列表。"""
        files = []
        pcl = (self.cfg.get("pcl_dir") or "").strip()
        mc = (self.cfg.get("mc_dir") or "").strip()
        if pcl and os.path.isdir(pcl):
            files += [os.path.join(pcl, "Log1.txt"), os.path.join(pcl, "Log2.txt")]
        if mc and os.path.isdir(mc):
            files += [
                os.path.join(mc, "logs", "latest.log"),
                os.path.join(mc, "logs", "debug.log"),
            ]
            cr = os.path.join(mc, "crash-reports")
            if os.path.isdir(cr):
                try:
                    files += [os.path.join(cr, f) for f in os.listdir(cr)
                              if f.lower().endswith(".txt")]
                except OSError:
                    pass
            try:
                files += [os.path.join(mc, f) for f in os.listdir(mc)
                          if f.lower().startswith("hs_err_pid") and
                          f.lower().endswith(".log")]
            except OSError:
                pass
        return files

    def _snapshot(self):
        """返回 {路径: (mtime, size)}，只保留存在的文件。"""
        snap = {}
        for p in self._watched():
            try:
                st = os.stat(p)
                snap[p] = (st.st_mtime, st.st_size)
            except OSError:
                continue
        return snap

    # ------------------------------------------------------------ 主循环

    def run(self):
        last = self._snapshot()
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if self._stop.is_set():
                break
            try:
                now = self._snapshot()
            except Exception:
                continue
            if now != last:
                last = now
                # 去抖：等写入稳定后再触发回调
                for _ in range(6):
                    self._stop.wait(self.debounce)
                    if self._stop.is_set():
                        return
                    stable = self._snapshot()
                    if stable == last:
                        break
                    last = stable
                if not self._stop.is_set():
                    try:
                        self.on_change()
                    except Exception:
                        pass

    def stop(self):
        self._stop.set()


class PclProcessWatcher(threading.Thread):
    """监控 PCL2 进程：一旦检测到运行中的 PCL2，回调其 exe 路径并停止。

    detector 可注入（测试用）；默认使用 config.detect_pcl_process。
    """

    def __init__(self, cfg: dict, on_found, interval: float = 6.0,
                 detector=None):
        super().__init__(daemon=True, name="pcl-watcher")
        self.cfg = cfg
        self.on_found = on_found
        self.interval = float(interval)
        self.detector = detector or config.detect_pcl_process
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if self._stop.is_set():
                return
            try:
                exe = self.detector()
            except Exception:
                continue
            if exe and os.path.isfile(exe):
                try:
                    self.on_found(exe)
                except Exception:
                    pass
                return  # 已定位，使命完成

    def stop(self):
        self._stop.set()
