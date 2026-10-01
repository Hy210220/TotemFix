# -*- coding: utf-8 -*-
"""修复执行器测试：备份、执行、路径安全、还原。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import fixer  # noqa: E402


class FixerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ed-fix-")
        self.mc = os.path.join(self.tmp, ".minecraft")
        os.makedirs(os.path.join(self.mc, "config"))
        os.makedirs(os.path.join(self.mc, "mods"))
        with open(os.path.join(self.mc, "mods", "bad.jar"), "wb") as f:
            f.write(b"bad")
        with open(os.path.join(self.mc, "config", "foo.toml"), "w",
                  encoding="utf-8") as f:
            f.write("renderDistance = 32\n")
        with open(os.path.join(self.mc, "options.txt"), "w", encoding="utf-8") as f:
            f.write("fov:0.5\n")
        self.ex = fixer.FixExecutor(self.mc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_with_backup(self):
        res = self.ex.execute([{"action": "delete", "path": "mods/bad.jar",
                                "reason": "冲突"}])
        self.assertEqual(res["ok"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.mc, "mods", "bad.jar")))
        self.assertTrue(res["results"][0]["backup"])
        backups = self.ex.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["orig"], "mods/bad.jar")

    def test_rename(self):
        res = self.ex.execute([{"action": "rename", "path": "config/foo.toml",
                                "new_name": "foo.toml.disabled"}])
        self.assertEqual(res["ok"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.mc, "config",
                                                    "foo.toml.disabled")))

    def test_edit_replace(self):
        res = self.ex.execute([{"action": "edit", "path": "options.txt",
                                "old_text": "fov:0.5", "new_text": "fov:1.0"}])
        self.assertEqual(res["ok"], 1)
        with open(os.path.join(self.mc, "options.txt"), encoding="utf-8") as f:
            self.assertIn("fov:1.0", f.read())

    def test_edit_not_found_skips(self):
        res = self.ex.execute([{"action": "edit", "path": "options.txt",
                                "old_text": "不存在的文本", "new_text": "x"}])
        self.assertEqual(res["ok"], 0)
        self.assertIn("未在文件中找到", res["results"][0]["message"])

    def test_write_new_file(self):
        res = self.ex.execute([{"action": "write", "path": "newfolder/a.txt",
                                "new_text": "hello"}])
        self.assertEqual(res["ok"], 1)
        with open(os.path.join(self.mc, "newfolder", "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello")

    def test_path_traversal_rejected(self):
        res = self.ex.execute([{"action": "delete", "path": "../evil.txt"}])
        self.assertEqual(res["ok"], 0)
        self.assertIn("非法", res["results"][0]["message"])

    def test_absolute_path_rejected(self):
        abs_path = os.path.join(self.tmp, "outside.txt")
        with open(abs_path, "w") as f:
            f.write("x")
        res = self.ex.execute([{"action": "delete", "path": abs_path}])
        self.assertEqual(res["ok"], 0)
        self.assertTrue(os.path.exists(abs_path))

    def test_unknown_action_skipped(self):
        res = self.ex.execute([{"action": "rm-rf", "path": "mods"}])
        self.assertEqual(res["ok"], 0)

    def test_restore_backup(self):
        self.ex.execute([{"action": "delete", "path": "mods/bad.jar"}])
        backups = self.ex.list_backups()
        orig = self.ex.restore(backups[0]["backup"])
        self.assertEqual(orig, "mods/bad.jar")
        self.assertTrue(os.path.exists(os.path.join(self.mc, "mods", "bad.jar")))

    def test_nonempty_dir_delete_refused(self):
        res = self.ex.execute([{"action": "delete", "path": "config"}])
        self.assertEqual(res["ok"], 0)
        self.assertIn("拒绝删除", res["results"][0]["message"])


if __name__ == "__main__":
    unittest.main()
