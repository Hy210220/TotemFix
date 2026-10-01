# -*- coding: utf-8 -*-
"""DeepSeek（OpenAI 兼容）API 客户端与结构化结果解析。"""

import json
import urllib.request
import urllib.error


class DeepSeekError(Exception):
    """调用 DeepSeek 失败时抛出，message 面向用户（中文）。"""


SYSTEM_PROMPT = """你是一名 Minecraft 故障诊断专家，擅长分析 PCL2 启动器与 Minecraft 的日志、崩溃报告。
用户会提供一段报错日志。请只输出一个严格的 JSON 对象（不要输出 JSON 以外的任何文字、注释或 Markdown 代码块），字段如下：
{
  "summary": "用一句话概括这次报错（中文）",
  "cause": "出错的根本原因，准确、精炼、面向普通玩家（中文）",
  "error_file": "出错或需要修改的文件/目录。若在游戏目录内，请给出相对于 .minecraft 的路径（如 config/xxx.toml、mods/xxx.jar、options.txt）；若无法确定则填空字符串",
  "solution_steps": ["建议的解决办法步骤 1", "步骤 2", "……"],
  "fix_plan": [
    {
      "action": "delete | rename | edit | write",
      "path": "相对 .minecraft 的路径，禁止出现盘符、绝对路径或 ..（例如 options.txt、config/xxx.toml）",
      "reason": "为什么这样改（中文）",
      "new_name": "仅 action=rename 时：新文件名（不含目录）",
      "old_text": "仅 action=edit 时：文件中要替换的原文片段",
      "new_text": "仅 action=edit/write 时：替换后的内容（edit）或完整的新文件内容（write）"
    }
  ],
  "confidence": "high | medium | low"
}
规则：
1. fix_plan 中每一项必须是针对单个文件的删除、重命名、文本替换或整文件覆盖写入；path 必须是 .minecraft 下的相对路径，禁止 ..、盘符与绝对路径。
2. 没有把握的修改不要放进 fix_plan，保持空数组，把建议写进 solution_steps 即可。fix_plan 宁可少而准。
3. 若日志信息不足，请基于 Minecraft 常见故障经验给出最可能的原因与排查步骤，并在 cause 中注明属于推测。
4. 文本替换（edit）的 old_text 必须来自日志/已知文件内容，不要凭空捏造；没有把握就改用 solution_steps 引导用户手动操作。
5. 答案整体使用简体中文。你正在以 json_object 模式输出，最终回复必须是一段合法 json，其中不能包含任何 markdown 标记。"""


def build_user_prompt(excerpt: str, meta: dict) -> str:
    parts = ["请分析下面这段 Minecraft/PCL2 报错日志：", "", "```", excerpt, "```"]
    if meta.get("pcl_version"):
        parts.append("\n环境信息：PCL 版本 " + str(meta["pcl_version"]))
    if meta.get("mc_version"):
        parts.append("Minecraft 版本 " + str(meta["mc_version"]))
    parts.append("\n请严格按照系统提示输出 JSON。")
    return "\n".join(parts)


def _normalize_base(base: str) -> str:
    base = (base or "https://api.deepseek.com").strip().rstrip("/")
    if base.endswith("/v1"):
        return base
    if base.endswith("/chat/completions"):
        return base[: -len("/chat/completions")]
    return base + "/v1"


class DeepSeekClient:
    def __init__(self, api_base: str, api_key: str, model: str = "deepseek-chat",
                 timeout: int = 300):
        self.base = _normalize_base(api_base)
        self.key = (api_key or "").strip()
        self.model = model or "deepseek-chat"
        self.timeout = timeout

    # ------------------------------------------------------------ 请求

    def _post(self, payload: dict) -> dict:
        if not self.key:
            raise DeepSeekError("尚未填写 DeepSeek API Key，请到“设置”中配置（sk-...）。")
        url = self.base + "/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.key,
            }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code == 400:
                raise DeepSeekError(
                    f"DeepSeek 拒绝了请求（400）：{detail or '参数可能有误，请检查模型名与 API 地址'}")
            if e.code == 401:
                raise DeepSeekError("API Key 无效或已过期（401），请检查“设置”中的密钥。")
            if e.code == 429:
                raise DeepSeekError("请求过于频繁或余额不足（429），请稍后再试或检查账户余额。")
            if e.code == 402:
                raise DeepSeekError("DeepSeek 账户余额不足（402），请先充值。")
            raise DeepSeekError(f"DeepSeek 接口返回错误 {e.code}：{detail or e.reason}")
        except urllib.error.URLError as e:
            raise DeepSeekError(f"无法连接 DeepSeek 服务器：{e.reason}。请检查网络或 API 地址。")
        except Exception as e:
            raise DeepSeekError(f"请求失败：{e}")
        try:
            return json.loads(body)
        except ValueError:
            raise DeepSeekError("DeepSeek 返回了无法解析的内容。")

    # ------------------------------------------------------------ 分析

    def analyze(self, excerpt: str, meta: dict = None) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(excerpt, meta or {})},
            ],
            "temperature": 0.2,
            "max_tokens": 4000,
            "stream": False,
        }
        # deepseek-reasoner 不支持 json_object 模式，靠提示词约束 JSON
        if "reasoner" not in self.model.lower():
            payload["response_format"] = {"type": "json_object"}
        resp = self._post(payload)
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise DeepSeekError("DeepSeek 返回内容缺少 choices 字段，请换一个模型重试。")
        return self._parse(content)

    # ------------------------------------------------------------ 对话

    def chat(self, history: list, question: str) -> str:
        """AI 问答（非 JSON 模式）。history: [{"role":"user"/"assistant","content":...}]"""
        messages = [
            {"role": "system",
             "content": "你是 Minecraft / PCL2 启动器故障排查助手，回答使用简体中文，"
                        "条理清晰、面向普通玩家，可给出具体文件路径与操作步骤。"},
        ]
        for h in (history or [])[-20:]:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)[:6000]})
        messages.append({"role": "user", "content": str(question)[:6000]})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 2000,
            "stream": False,
        }
        resp = self._post(payload)
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise DeepSeekError("DeepSeek 返回内容异常，请重试。")

    # ------------------------------------------------------------ 解析

    @staticmethod
    def _parse(content: str) -> dict:
        text = (content or "").strip()
        # 去掉可能的 ```json ... ``` 围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            obj = json.loads(text)
        except ValueError:
            # 尝试截取第一个 { 到最后一个 }
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e > s:
                try:
                    obj = json.loads(text[s:e + 1])
                except ValueError:
                    obj = None
            else:
                obj = None
        if not isinstance(obj, dict):
            raise DeepSeekError("AI 返回的内容不是有效 JSON，请重试（可换 deepseek-chat 模型）。")

        plan = []
        for item in obj.get("fix_plan") or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "")).strip().lower()
            if action not in ("delete", "rename", "edit", "write"):
                continue
            path = str(item.get("path", "")).strip()
            if not path or ".." in path.replace("\\", "/").split("/"):
                continue
            clean = {
                "action": action,
                "path": path.replace("\\", "/"),
                "reason": str(item.get("reason", ""))[:500],
            }
            if action == "rename":
                clean["new_name"] = str(item.get("new_name", "")).strip()[:200]
                if not clean["new_name"] or "/" in clean["new_name"] or "\\" in clean["new_name"]:
                    continue
            if action == "edit":
                clean["old_text"] = str(item.get("old_text", ""))
                clean["new_text"] = str(item.get("new_text", ""))
                if not clean["old_text"]:
                    continue
            if action == "write":
                clean["new_text"] = str(item.get("new_text", ""))
            plan.append(clean)

        steps = []
        for s in obj.get("solution_steps") or []:
            s = str(s).strip()
            if s:
                steps.append(s[:1000])

        return {
            "summary": str(obj.get("summary", ""))[:500],
            "cause": str(obj.get("cause", ""))[:3000],
            "error_file": str(obj.get("error_file", ""))[:500],
            "solution_steps": steps[:12],
            "fix_plan": plan[:8],
            "confidence": str(obj.get("confidence", "medium")).lower()[:20],
            "raw": content[:4000],
        }
