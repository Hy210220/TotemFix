# -*- coding: utf-8 -*-
"""自动流程引擎测试：扫描→新报错→分析→修复→再扫描去重，全部走事件回调。"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import engine as engine_mod  # noqa: E402


class FakeClient:
    """模拟 DeepSeek 客户端（不联网）。"""

    def __init__(self, cfg):
        self.cfg = dict(cfg)

    def analyze(self, excerpt, meta):
        if "FakeFail" in excerpt:
            from errordoctor import deepseek
            raise deepseek.DeepSeekError("模拟分析失败")
        return {
            "summary": "模拟总结",
            "cause": "模拟原因",
            "error_file": "config.txt",
            "solution_steps": ["模拟步骤 1", "模拟步骤 2"],
            "fix_plan": [{"action": "edit", "path": "config.txt",
                          "old_text": "old", "new_text": "ai-fixed",
                          "reason": "测试替换"}],
            "confidence": "high",
        }

    def chat(self, history, question):
        return "模拟回复：" + question


def wait_for(events, pred, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with events_lock:
            for e in list(events):
                if pred(e):
                    return e
        time.sleep(0.05)
    return None


events_lock = threading.Lock()


class EngineTest(unittest.TestCase):
    def setUp(self):
        global events_lock
        self.tmp = tempfile.mkdtemp(prefix="ed-engine-")
        self.pcl = os.path.join(self.tmp, "PCL")
        self.mc = os.path.join(self.tmp, ".minecraft")
        os.makedirs(os.path.join(self.mc, "logs"))
        os.makedirs(self.pcl)
        with open(os.path.join(self.mc, "config.txt"), "w", encoding="utf-8") as f:
            f.write("old\n")
        self.events = []
        events_lock = threading.Lock()
        self.cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc,
                    "api_base": "http://fake", "api_key": "sk-fake",
                    "model": "deepseek-chat", "watch_interval": 0.1}
        self.engine = engine_mod.Engine(self.cfg, on_event=self._on_event,
                                        client_factory=lambda c: FakeClient(c))

    def tearDown(self):
        self.engine.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _on_event(self, ev):
        with events_lock:
            self.events.append(ev)

    def _clear_events(self):
        with events_lock:
            self.events.clear()

    def test_full_auto_flow(self):
        # 初始：无报错
        self.engine.scan("startup")
        ev = wait_for(self.events, lambda e: e["type"] == "scan_done")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["result"]["stats"]["issue_count"], 0)

        # 模拟游戏崩溃 → 触发新报错
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\njava.lang.NullPointerException: test\n"
                    "\tat net.minecraft.server.Main.main(Main.java:1)\n")
        self._clear_events()
        self.engine.scan("watch")
        ev = wait_for(self.events, lambda e: e["type"] == "new_issues")
        self.assertIsNotNone(ev, "应产生 new_issues 事件")
        new_issues = ev["issues"]
        self.assertGreaterEqual(len(new_issues), 1)

        # 自动分析
        self.engine.analyze(new_issues)
        ev = wait_for(self.events, lambda e: e["type"] == "analyze_done")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["ok_count"], 1)
        sig = new_issues[0]["sig"]
        self.assertIn("fix_plan", self.engine.analyses[sig])

        # 自动修复
        plan = self.engine.analyses[sig]["fix_plan"]
        self.engine.apply(plan, reason="auto")
        ev = wait_for(self.events, lambda e: e["type"] == "applied")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["result"]["ok"], 1)
        cfg_file = os.path.join(self.mc, "config.txt")
        with open(cfg_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ai-fixed\n")

        # 修复后再次扫描：同样日志不再算新报错（去重）
        self._clear_events()
        self.engine.scan("after_fix")
        ev = wait_for(self.events, lambda e: e["type"] == "scan_done")
        self.assertIsNotNone(ev)
        time.sleep(0.2)
        with events_lock:
            types = [e["type"] for e in self.events]
        self.assertNotIn("new_issues", types, "旧报错不应重复触发")

        # 备份与还原
        backups = self.engine.list_backups()
        self.assertGreaterEqual(len(backups), 1)
        orig = self.engine.restore(backups[0]["backup"])
        self.assertEqual(orig, "config.txt")
        with open(cfg_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\n")

    def test_new_error_after_previous_is_new(self):
        self.engine.scan("startup")
        wait_for(self.events, lambda e: e["type"] == "scan_done")
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\njava.lang.RuntimeException: first\n")
        self._clear_events()
        self.engine.scan("watch")
        wait_for(self.events, lambda e: e["type"] == "new_issues")
        # 出现不同标题的新报错 → 再次触发
        self._clear_events()
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\njava.lang.OutOfMemoryError: second\n")
        self.engine.scan("watch")
        ev = wait_for(self.events, lambda e: e["type"] == "new_issues")
        self.assertIsNotNone(ev, "不同报错应再次触发 new_issues")

    def test_same_error_with_new_timestamp_not_retriggered(self):
        """同一报错仅时间戳不同 → 签名归一化后不重复触发。"""
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n[12:00:00] [ERROR]: Something failed\n")
        self.engine.scan("startup")
        wait_for(self.events, lambda e: e["type"] == "new_issues")
        self._clear_events()
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n[13:45:00] [ERROR]: Something failed\n")
        self.engine.scan("watch")
        wait_for(self.events, lambda e: e["type"] == "scan_done")
        time.sleep(0.2)
        with events_lock:
            types = [e["type"] for e in self.events]
        self.assertNotIn("new_issues", types)

    def test_analyze_failure_recorded(self):
        with open(os.path.join(self.mc, "logs", "latest.log"), "a",
                  encoding="utf-8") as f:
            f.write("\njava.lang.Exception: FakeFail\n")
        self.engine.scan("startup")
        ev = wait_for(self.events, lambda e: e["type"] == "new_issues")
        self.engine.analyze(ev["issues"])
        ev = wait_for(self.events, lambda e: e["type"] == "analyze_done")
        self.assertEqual(ev["fail_count"], 1)
        self.assertIn("error", list(self.engine.analyses.values())[0])

    def test_chat(self):
        self.engine.chat([{"role": "user", "content": "你好"}], "怎么装光影？")
        ev = wait_for(self.events, lambda e: e["type"] == "chat_reply")
        self.assertIsNotNone(ev)
        self.assertIn("怎么装光影", ev["reply"])

    def test_apply_rejects_unsafe(self):
        self.engine.apply([{"action": "delete", "path": "../escape.txt"}])
        ev = wait_for(self.events, lambda e: e["type"] == "applied")
        self.assertEqual(ev["result"]["ok"], 0)
        self.assertIn("非法", ev["result"]["results"][0]["message"])


if __name__ == "__main__":
    unittest.main()
