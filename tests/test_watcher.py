# -*- coding: utf-8 -*-
"""文件监控器测试：文件变化触发 on_change，且去抖合并连续写入。"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import watcher  # noqa: E402


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ed-watch-")
        self.pcl = os.path.join(self.tmp, "PCL")
        self.mc = os.path.join(self.tmp, ".minecraft")
        os.makedirs(os.path.join(self.mc, "logs"))
        os.makedirs(self.pcl)
        with open(os.path.join(self.mc, "logs", "latest.log"), "w",
                  encoding="utf-8") as f:
            f.write("hello\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start(self):
        cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc, "watch_interval": 0.1}
        hits = []
        lock = threading.Lock()

        def on_change():
            with lock:
                hits.append(time.time())

        w = watcher.Watcher(cfg, on_change, interval=0.1, debounce=0.1)
        w.start()
        return w, hits, lock

    def _wait_hits(self, hits, lock, n, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                if len(hits) >= n:
                    return True
            time.sleep(0.05)
        return False

    def test_change_triggers_callback(self):
        w, hits, lock = self._start()
        try:
            time.sleep(0.35)
            with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                      encoding="utf-8") as f:
                f.write("world\n")
            self.assertTrue(self._wait_hits(hits, lock, 1),
                            "修改 latest.log 应触发 on_change")
        finally:
            w.stop()

    def test_continuous_write_debounced(self):
        """连续写入应被去抖合并，而不是每次都触发。"""
        w, hits, lock = self._start()
        try:
            time.sleep(0.35)
            p = os.path.join(self.mc, "logs", "latest.log")
            for _ in range(8):  # 8 次连续写入，间隔 < debounce
                with open(p, "a", encoding="utf-8") as f:
                    f.write("x")
                time.sleep(0.03)
            time.sleep(1.0)
            with lock:
                n = len(hits)
            self.assertLessEqual(n, 2, f"连续写入应被去抖（实际触发 {n} 次）")
            self.assertGreaterEqual(n, 1)
        finally:
            w.stop()

    def test_new_crash_report_detected(self):
        w, hits, lock = self._start()
        try:
            time.sleep(0.35)
            os.makedirs(os.path.join(self.mc, "crash-reports"), exist_ok=True)
            with open(os.path.join(self.mc, "crash-reports", "crash.txt"), "w",
                      encoding="utf-8") as f:
                f.write("boom")
            self.assertTrue(self._wait_hits(hits, lock, 1),
                            "新增崩溃报告应触发 on_change")
        finally:
            w.stop()

    def test_no_change_no_callback(self):
        w, hits, lock = self._start()
        try:
            time.sleep(1.0)
            with lock:
                self.assertEqual(len(hits), 0, "无变化时不应触发回调")
        finally:
            w.stop()


if __name__ == "__main__":
    unittest.main()
