# -*- coding: utf-8 -*-
"""扫描器单元测试：用临时目录构造各类日志。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import scanner  # noqa: E402


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ed-test-")
        self.pcl = os.path.join(self.tmp, "PCL")
        self.mc = os.path.join(self.tmp, ".minecraft")
        os.makedirs(os.path.join(self.mc, "logs"))
        os.makedirs(os.path.join(self.mc, "crash-reports"))
        os.makedirs(self.pcl)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_scan_pcl_exit_code(self):
        self._write(os.path.join(self.pcl, "Log1.txt"),
                    "[11:22:33] 启动游戏\n[11:22:35] 游戏已退出，退出代码 -1\n")
        cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc}
        res = scanner.scan(cfg)
        self.assertGreaterEqual(res["stats"]["issue_count"], 1)
        self.assertTrue(any(i["kind"] == "退出代码" and i["severity"] >= 80
                            for i in res["issues"]))

    def test_scan_game_exception_with_stack(self):
        log = "\n".join([
            "[12:00:00] [main/INFO]: Hello",
            "[12:00:01] [main/ERROR]: Failed to load mods",
            "java.lang.NullPointerException: Cannot invoke mod loader",
            "\tat net.minecraft.server.Main.main(Main.java:42)",
            "\tat java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)",
            "\tat ModLoader.load(ModLoader.java:100)",
            "[12:00:02] [main/INFO]: Bye",
        ])
        self._write(os.path.join(self.mc, "logs", "latest.log"), log)
        cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc}
        res = scanner.scan(cfg)
        exc = [i for i in res["issues"] if i["kind"] == "异常"]
        self.assertTrue(exc)
        self.assertIn("NullPointerException", exc[0]["excerpt"])
        self.assertIn("at net.minecraft.server.Main.main", exc[0]["excerpt"])

    def test_scan_crash_report(self):
        report = """---- Minecraft Crash Report ----
// Don't do that.

Time: 2025-01-01 00:00:00
Description: Ticking entity

java.lang.RuntimeException: bad entity
\tat net.minecraft.server.level.ServerLevel.tick(ServerLevel.java:99)

A detailed walkthrough...
"""
        self._write(os.path.join(self.mc, "crash-reports", "crash-2025-01-01.txt"),
                    report)
        cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc}
        res = scanner.scan(cfg)
        self.assertTrue(any(i["kind"] == "崩溃报告" and i["severity"] == 100
                            for i in res["issues"]))
        self.assertTrue(any("Ticking entity" in i["title"] for i in res["issues"]))

    def test_scan_no_config_reports_errors(self):
        res = scanner.scan({"pcl_dir": "", "mc_dir": ""})
        self.assertTrue(res["errors"])
        self.assertEqual(res["stats"]["issue_count"], 0)

    def test_scan_gbk_encoding(self):
        self._write(os.path.join(self.pcl, "Log1.txt"),
                    "[10:00:00] 游戏崩溃\n".encode("gbk").decode("gbk"))
        # 以 gbk 字节写入
        with open(os.path.join(self.pcl, "Log1.txt"), "wb") as f:
            f.write("[10:00:00] 游戏崩溃，退出代码 1\n".encode("gbk"))
        cfg = {"pcl_dir": self.pcl, "mc_dir": self.mc}
        res = scanner.scan(cfg)
        self.assertGreaterEqual(res["stats"]["issue_count"], 1)


if __name__ == "__main__":
    unittest.main()
