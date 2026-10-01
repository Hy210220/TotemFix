# -*- coding: utf-8 -*-
"""DeepSeek 客户端测试：本地 mock 服务 + 解析函数测试。"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import deepseek  # noqa: E402


class MockHandler(BaseHTTPRequestHandler):
    mode = "ok"           # ok | fenced | http401 | http402 | http429 | badjson
    last_request = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        MockHandler.last_request = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        }
        reply = {
            "summary": "某个 Mod 与当前版本冲突",
            "cause": "mods/bad.jar 与 1.20.1 不兼容",
            "error_file": "mods/bad.jar",
            "solution_steps": ["删除或替换该 Mod", "重新启动游戏"],
            "fix_plan": [{"action": "delete", "path": "mods/bad.jar",
                          "reason": "不兼容"}],
            "confidence": "high",
        }
        if self.mode == "http401":
            self.send_response(401); self.end_headers(); return
        if self.mode == "http402":
            self.send_response(402); self.end_headers(); return
        if self.mode == "http429":
            self.send_response(429); self.end_headers(); return
        content = json.dumps(reply, ensure_ascii=False)
        if self.mode == "fenced":
            content = "```json\n" + content + "\n```"
        if self.mode == "badjson":
            content = "抱歉，我不是 JSON"
        resp = {"choices": [{"message": {"content": content}}]}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_mock():
    srv = HTTPServer(("127.0.0.1", 0), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


class DeepSeekClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start_mock()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _client(self):
        return deepseek.DeepSeekClient(
            f"http://127.0.0.1:{self.port}", "sk-test-key", "deepseek-chat")

    def test_analyze_ok(self):
        MockHandler.mode = "ok"
        res = self._client().analyze("java.lang.NullPointerException")
        self.assertEqual(res["summary"], "某个 Mod 与当前版本冲突")
        self.assertEqual(res["fix_plan"][0]["action"], "delete")
        self.assertEqual(MockHandler.last_request["auth"], "Bearer sk-test-key")
        self.assertEqual(MockHandler.last_request["body"]["model"], "deepseek-chat")
        self.assertEqual(MockHandler.last_request["body"]["response_format"],
                         {"type": "json_object"})
        self.assertIn("json", MockHandler.last_request["body"]["messages"][0]["content"])

    def test_analyze_fenced_json(self):
        MockHandler.mode = "fenced"
        res = self._client().analyze("...")
        self.assertEqual(res["confidence"], "high")

    def test_analyze_http_errors(self):
        MockHandler.mode = "http401"
        with self.assertRaises(deepseek.DeepSeekError) as cm:
            self._client().analyze("...")
        self.assertIn("API Key", str(cm.exception))
        MockHandler.mode = "http402"
        with self.assertRaises(deepseek.DeepSeekError) as cm:
            self._client().analyze("...")
        self.assertIn("余额", str(cm.exception))
        MockHandler.mode = "http429"
        with self.assertRaises(deepseek.DeepSeekError):
            self._client().analyze("...")

    def test_analyze_bad_json(self):
        MockHandler.mode = "badjson"
        with self.assertRaises(deepseek.DeepSeekError):
            self._client().analyze("...")

    def test_missing_key(self):
        c = deepseek.DeepSeekClient("https://api.deepseek.com", "")
        with self.assertRaises(deepseek.DeepSeekError) as cm:
            c._post({})
        self.assertIn("API Key", str(cm.exception))

    def test_parse_drops_unsafe_plan_items(self):
        content = json.dumps({
            "summary": "s", "cause": "c", "error_file": "",
            "solution_steps": ["x"],
            "fix_plan": [
                {"action": "delete", "path": "../evil.jar", "reason": "r"},
                {"action": "rm", "path": "a.jar", "reason": "r"},
                {"action": "delete", "path": "mods/ok.jar", "reason": "r"},
                {"action": "rename", "path": "mods/x.jar", "new_name": "../y"},
            ],
            "confidence": "high",
        }, ensure_ascii=False)
        res = deepseek.DeepSeekClient._parse(content)
        self.assertEqual(len(res["fix_plan"]), 1)
        self.assertEqual(res["fix_plan"][0]["path"], "mods/ok.jar")

    def test_parse_plain_json_fallback(self):
        res = deepseek.DeepSeekClient._parse(
            '前言 {"summary":"s","cause":"c","solution_steps":[],'
            '"fix_plan":[],"confidence":"medium"} 后语')
        self.assertEqual(res["summary"], "s")


if __name__ == "__main__":
    unittest.main()
