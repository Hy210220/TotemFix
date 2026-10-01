# -*- coding: utf-8 -*-
"""后台文件监控器（轮询实现，零依赖）。

监控 PCL 日志、游戏日志、崩溃报告目录、JVM 崩溃日志。
文件新增/修改/大小变化 → 触发 on_change（已内置去抖，写入期间不会反复触发）。
"""

import os
import threading
import time


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
