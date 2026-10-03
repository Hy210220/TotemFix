# -*- coding: utf-8 -*-
"""内置资料库测试：条目加载、离线匹配、多命中限制。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import knowledge  # noqa: E402


class KnowledgeTest(unittest.TestCase):
    def test_entries_loaded(self):
        self.assertGreaterEqual(knowledge.entry_count(), 18,
                                "资料库至少应包含 18 条常见报错")

    def test_match_glfw(self):
        hits = knowledge.match("Failed to create the GLFW window\nGLFW error 65543")
        ids = [h["id"] for h in hits]
        self.assertIn("glfw-65543", ids)

    def test_match_oom(self):
        hits = knowledge.match("java.lang.OutOfMemoryError: Java heap space")
        self.assertEqual(hits[0]["id"], "oom")

    def test_match_exit_code_1(self):
        hits = knowledge.match("[12:00:00] 游戏已退出，退出代码 1")
        self.assertEqual(hits[0]["id"], "exit-code-1")

    def test_match_lan(self):
        """联机类条目：PCL2 联机失败。"""
        hits = knowledge.match("PCL2 联机失败，无法加入联机房间")
        self.assertEqual(hits[0]["id"], "pcl-link-fail")

    def test_match_invalid_session(self):
        hits = knowledge.match("Failed to verify username! Invalid session")
        self.assertEqual(hits[0]["id"], "invalid-session")

    def test_match_port_in_use(self):
        hits = knowledge.match("java.net.BindException: Address already in use")
        self.assertEqual(hits[0]["id"], "port-in-use")

    def test_match_nat(self):
        hits = knowledge.match("外网进不来，需要端口映射 NAT 穿透")
        self.assertEqual(hits[0]["id"], "nat-port")

    def test_match_shader(self):
        hits = knowledge.match("OptiFine 与光影包冲突导致黑屏")
        self.assertEqual(hits[0]["id"], "shader-optifine")

    def test_match_forge_install(self):
        hits = knowledge.match("Failed to download file, Forge install 失败")
        self.assertEqual(hits[0]["id"], "forge-install-fail")

    def test_match_java_path(self):
        hits = knowledge.match("java.nio.file.InvalidPathException: Illegal char")
        self.assertEqual(hits[0]["id"], "java-path-invalid")

    def test_match_ms_login(self):
        hits = knowledge.match("正版登录尝试失败：错误码 BadRequest（400）")
        self.assertEqual(hits[0]["id"], "ms-login-fail")

    def test_match_world_corrupt(self):
        hits = knowledge.match("Failed to load world: level.dat 损坏")
        self.assertEqual(hits[0]["id"], "world-corrupt")

    def test_match_resource_pack(self):
        hits = knowledge.match("Failed to load resource pack: 资源包加载失败")
        self.assertEqual(hits[0]["id"], "resource-pack-fail")

    def test_no_match(self):
        self.assertEqual(knowledge.match("今天天气不错，游戏正常运行"), [])

    def test_match_limit(self):
        """同时命中多条时最多返回 3 条。"""
        text = "exit code 1 and Connection timed out and NoClassDefFoundError and GLFW error 65543"
        hits = knowledge.match(text)
        self.assertLessEqual(len(hits), 3)
        self.assertGreaterEqual(len(hits), 1)

    def test_match_ci(self):
        """匹配忽略大小写。"""
        self.assertTrue(knowledge.match("EXIT CODE 1"))
        self.assertTrue(knowledge.match("glfw error 65543"))

    def test_entry_structure(self):
        for e in knowledge.get_kb().entries:
            self.assertTrue(e.get("title"))
            self.assertTrue(e.get("patterns"))
            self.assertIsInstance(e.get("solution"), list)
            self.assertTrue(e.get("solution"))


if __name__ == "__main__":
    unittest.main()
