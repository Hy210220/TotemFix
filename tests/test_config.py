# -*- coding: utf-8 -*-
"""配置检测测试：PCL exe 名兼容、Setup.ini/注册表解析、多 .minecraft 候选、
全盘扫描、进程检测（mock powershell）。"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ed-cfg-")
        self.pcl = os.path.join(self.tmp, "PCL")
        os.makedirs(self.pcl)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    # ------------------------------------------------ PCL 目录识别

    def test_is_pcl_dir_full_exe_name(self):
        """官方新版 exe 名为 Plain Craft Launcher 2.exe。"""
        self._write(os.path.join(self.pcl, "Plain Craft Launcher 2.exe"), "x")
        self.assertTrue(config._is_pcl_dir(self.pcl))

    def test_is_pcl_dir_short_exe_name(self):
        self._write(os.path.join(self.pcl, "PCL.exe"), "x")
        self.assertTrue(config._is_pcl_dir(self.pcl))

    def test_is_pcl_dir_log_only(self):
        """只有 Log1.txt 也应识别（老版本/便携版）。"""
        self._write(os.path.join(self.pcl, "Log1.txt"), "x")
        self.assertTrue(config._is_pcl_dir(self.pcl))

    def test_not_pcl_dir(self):
        self.assertFalse(config._is_pcl_dir(self.tmp))

    def test_detect_pcl_process_by_title(self):
        """mock powershell：按窗口标题识别运行中的 PCL2。"""
        exe = os.path.join(self.pcl, "Plain Craft Launcher 2.exe")
        self._write(exe, "x")
        with mock.patch("errordoctor.config._run", return_value=exe), \
                mock.patch("errordoctor.config.os.name", "nt"):
            self.assertEqual(config.detect_pcl_process(), exe)

    def test_detect_pcl_process_rejects_unrelated(self):
        """窗口标题匹配到无关程序时不应误判。"""
        other = os.path.join(self.tmp, "SomeApp.exe")
        self._write(other, "x")
        with mock.patch("errordoctor.config._run", return_value=other), \
                mock.patch("errordoctor.config.os.name", "nt"):
            self.assertEqual(config.detect_pcl_process(), "")

    # ------------------------------------------------ Setup.ini / 注册表

    def test_setup_ini_parse_and_dollar_resolve(self):
        self._write(os.path.join(self.pcl, "Setup.ini"),
                    "WindowHeight:550\r\nLaunchFolderSelect:$\\.minecraft\r\n")
        os.makedirs(os.path.join(self.pcl, ".minecraft"))
        dirs = config.read_pcl_game_dirs(self.pcl)
        self.assertEqual(dirs, [os.path.join(self.pcl, ".minecraft")])

    def test_registry_launch_folders_parse(self):
        """注册表 LaunchFolders：显示名>路径 条目，| 分隔。"""
        self._write(os.path.join(self.pcl, "Setup.ini"), "")
        self._write(os.path.join(self.tmp, "Games", ".minecraft"), "x")
        raw = f"我的世界>{os.path.join(self.tmp, 'Games', '.minecraft')}|整合包>{os.path.join(self.tmp, 'Games', '.minecraft')}"
        with mock.patch.object(config, "_read_registry_launch_folders",
                               return_value=[raw]):
            dirs = config.read_pcl_game_dirs(self.pcl)
        self.assertEqual(dirs[0], os.path.join(self.tmp, "Games", ".minecraft"))
        self.assertEqual(len(dirs), 2)

    # ------------------------------------------------ detect_mc_dirs 优先级

    def test_detect_mc_dirs_priority(self):
        """日志 > Setup.ini 当前选择 > 注册表列表 > 默认位置。"""
        log_dir = os.path.join(self.tmp, "LogMc", ".minecraft")
        sel_dir = os.path.join(self.pcl, ".minecraft")
        reg_dir = os.path.join(self.tmp, "RegMc", ".minecraft")
        for d in (log_dir, sel_dir, reg_dir):
            os.makedirs(d)
        self._write(os.path.join(self.pcl, "Log1.txt"),
                    f"[System] Minecraft 文件夹：{log_dir}\n")
        self._write(os.path.join(self.pcl, "Setup.ini"),
                    "LaunchFolderSelect:$\\.minecraft\n")
        with mock.patch.object(config, "_read_registry_launch_folders",
                               return_value=[f"备用>{reg_dir}"]):
            dirs = config.detect_mc_dirs(self.pcl)
        self.assertEqual(dirs[0], log_dir)
        self.assertEqual(dirs[1], sel_dir)
        self.assertEqual(dirs[2], reg_dir)

    def test_detect_mc_dirs_dedup(self):
        sel_dir = os.path.join(self.pcl, ".minecraft")
        os.makedirs(sel_dir)
        self._write(os.path.join(self.pcl, "Setup.ini"),
                    "LaunchFolderSelect:$\\.minecraft\n")
        self._write(os.path.join(self.pcl, "Log1.txt"),
                    f"[System] 文件夹：{sel_dir}\n")
        with mock.patch.object(config, "_read_registry_launch_folders",
                               return_value=[]):
            dirs = config.detect_mc_dirs(self.pcl)
        self.assertEqual(len([d for d in dirs if d == sel_dir]), 1)

    # ------------------------------------------------ 全盘扫描

    def test_scan_all_minecraft_dirs(self):
        tree = self.tmp
        found = {
            os.path.join(tree, ".minecraft"),                        # 深度 1
            os.path.join(tree, "Games", "MC", ".minecraft"),         # 深度 3
            os.path.join(tree, "a", "b", "c", "d", ".minecraft"),    # 深度 5
        }
        skipped = {
            os.path.join(tree, "node_modules", "x", ".minecraft"),   # 剪枝
            os.path.join(tree, ".hidden", ".minecraft"),             # 隐藏目录剪枝
            os.path.join(tree, "a", "b", "c", "d", "e", ".minecraft"),  # 超深
        }
        for d in found | skipped:
            os.makedirs(d, exist_ok=True)
        result = config.scan_all_minecraft_dirs(max_depth=5, roots=[tree])
        for d in found:
            self.assertIn(d, result, f"应找到 {d}")
        for d in skipped:
            self.assertNotIn(d, result, f"应剪枝 {d}")


if __name__ == "__main__":
    unittest.main()
