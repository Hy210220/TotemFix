# -*- coding: utf-8 -*-
"""修复计划执行器。

安全设计：
  * 仅允许操作 .minecraft 目录内的相对路径（拒绝绝对路径、盘符、.. 越界）；
  * 任何破坏性操作（删除/重命名/覆盖/改写）前先把原文件备份到
    <.minecraft>/errordoctor-backups/ 目录，文件名带时间戳；
  * 拒绝跟随符号链接逃出游戏目录。
"""

import json
import os
import re
import shutil
import time

ALLOWED_ACTIONS = ("delete", "rename", "edit", "write")
BACKUP_DIR_NAME = "errordoctor-backups"
MAX_EDIT_BYTES = 10 * 1024 * 1024


class FixError(Exception):
    """修复计划不合法或执行失败。"""


def _is_abs_windows(p: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[/\\]", p)) or p.startswith(("/", "\\"))


class FixExecutor:
    def __init__(self, mc_dir: str):
        if not mc_dir:
            raise FixError("未配置 Minecraft 文件夹路径。")
        self.mc_dir = os.path.abspath(mc_dir)
        if not os.path.isdir(self.mc_dir):
            raise FixError(f"Minecraft 文件夹不存在：{self.mc_dir}")
        self.backups_dir = os.path.join(self.mc_dir, BACKUP_DIR_NAME)

    # ------------------------------------------------------------ 路径校验

    def resolve(self, rel: str) -> str:
        p = str(rel or "").strip().replace("\\", "/").strip("/")
        if not p:
            raise FixError(f"路径为空：{rel!r}")
        if _is_abs_windows(p):
            raise FixError(f"仅允许 .minecraft 内的相对路径：{rel!r}")
        parts = p.split("/")
        if any(x in ("..", ".") for x in parts):
            raise FixError(f"路径包含非法片段：{rel!r}")
        full = os.path.abspath(os.path.join(self.mc_dir, *parts))
        try:
            if os.path.commonpath([full, self.mc_dir]) != self.mc_dir:
                raise FixError(f"路径越出游戏目录：{rel!r}")
        except ValueError:
            raise FixError(f"路径非法：{rel!r}")
        # 已存在且是符号链接 → 拒绝（防逃逸）
        if os.path.islink(full):
            raise FixError(f"拒绝操作符号链接：{rel!r}")
        return full

    # ------------------------------------------------------------ 备份

    def _index_path(self) -> str:
        return os.path.join(self.backups_dir, "_index.json")

    def backup(self, full: str) -> str:
        """复制原文件到备份目录并在索引中记录原路径，返回备份相对路径。"""
        if not os.path.isfile(full):
            return ""
        os.makedirs(self.backups_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        rel = os.path.relpath(full, self.mc_dir).replace("\\", "/")
        safe = re.sub(r"[^\w.\-]", "_", rel)
        dst_name = f"{ts}_{safe}.bak"
        dst = os.path.join(self.backups_dir, dst_name)
        shutil.copy2(full, dst)
        index = self._load_index()
        index[dst_name] = {"orig": rel, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._save_index(index)
        return os.path.join(BACKUP_DIR_NAME, dst_name)

    def _load_index(self) -> dict:
        try:
            with open(self._index_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_index(self, index: dict) -> None:
        try:
            with open(self._index_path(), "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def list_backups(self) -> list:
        """列出全部备份（新的在前）。"""
        index = self._load_index()
        out = []
        if os.path.isdir(self.backups_dir):
            for f in sorted(os.listdir(self.backups_dir), reverse=True):
                if not f.endswith(".bak"):
                    continue
                entry = index.get(f, {})
                out.append({
                    "backup": os.path.join(BACKUP_DIR_NAME, f),
                    "orig": entry.get("orig", "未知（索引缺失）"),
                    "time": entry.get("time", ""),
                    "size": os.path.getsize(os.path.join(self.backups_dir, f)),
                })
        return out

    def restore(self, backup_rel: str) -> str:
        """把某份备份恢复到原位置。返回原文件相对路径。"""
        p = str(backup_rel or "").strip().replace("\\", "/")
        if not p.startswith(BACKUP_DIR_NAME + "/") or not p.endswith(".bak"):
            raise FixError(f"不是有效的备份路径：{backup_rel!r}")
        src = os.path.abspath(os.path.join(self.mc_dir, *p.split("/")))
        if not os.path.isfile(src):
            raise FixError(f"备份文件不存在：{backup_rel!r}")
        index = self._load_index()
        entry = index.get(os.path.basename(p), {})
        orig_rel = str(entry.get("orig", "")).strip()
        if not orig_rel:
            raise FixError("索引中找不到该备份的原路径，无法自动还原")
        target = self.resolve(orig_rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        return os.path.relpath(target, self.mc_dir).replace("\\", "/")


# ---------------------------------------------------------------- 执行

    def _do(self, item: dict) -> dict:
        action = str(item.get("action", "")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            raise FixError(f"不支持的修复动作：{item.get('action')!r}")
        rel = str(item.get("path", "")).strip()
        full = self.resolve(rel)
        rel_out = os.path.relpath(full, self.mc_dir).replace("\\", "/")
        bak = self.backup(full)

        if action == "delete":
            if not os.path.exists(full):
                raise FixError("文件不存在，跳过删除")
            if os.path.isdir(full):
                if os.listdir(full):
                    raise FixError("目录非空，出于安全考虑拒绝删除")
                os.rmdir(full)
            else:
                os.remove(full)
            return {"path": rel_out, "action": "delete", "ok": True,
                    "message": f"已删除 {rel_out}", "backup": bak}

        if action == "rename":
            if not os.path.exists(full):
                raise FixError("文件不存在，跳过重命名")
            new_name = str(item.get("new_name", "")).strip()
            if not new_name or "/" in new_name or "\\" in new_name:
                raise FixError("rename 需要合法的 new_name")
            new_full = os.path.join(os.path.dirname(full), new_name)
            new_rel = os.path.relpath(new_full, self.mc_dir).replace("\\", "/")
            if os.path.exists(new_full):
                raise FixError(f"目标已存在，拒绝覆盖：{new_rel}")
            os.rename(full, new_full)
            return {"path": rel_out, "action": "rename", "ok": True,
                    "message": f"已重命名 {rel_out} → {new_rel}", "backup": bak}

        if action == "edit":
            if not os.path.isfile(full):
                raise FixError("文件不存在，跳过替换")
            if os.path.getsize(full) > MAX_EDIT_BYTES:
                raise FixError("文件过大（>10MB），拒绝自动替换")
            with open(full, "rb") as f:
                raw = f.read()
            for enc in ("utf-8", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = None
            if text is None:
                raise FixError("文件编码无法识别，拒绝自动替换")
            old = str(item.get("old_text", ""))
            new = str(item.get("new_text", ""))
            if not old:
                raise FixError("edit 需要 old_text")
            if old not in text:
                raise FixError("未在文件中找到要替换的原文，可能文件已被修改过")
            new_text = text.replace(old, new, 1)
            with open(full, "w", encoding=enc if enc != "gbk" else "gbk",
                      newline="") as f:
                f.write(new_text)
            return {"path": rel_out, "action": "edit", "ok": True,
                    "message": f"已替换 {rel_out} 中的 1 处文本", "backup": bak}

        if action == "write":
            new_text = str(item.get("new_text", ""))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            return {"path": rel_out, "action": "write", "ok": True,
                    "message": f"已写入 {rel_out}", "backup": bak}
        raise FixError("未知动作")

    def execute(self, plan_items: list) -> dict:
        """执行修复计划，逐项返回结果（单项失败不影响其它项）。"""
        results = []
        ok_count = 0
        for item in plan_items or []:
            try:
                r = self._do(item)
                ok_count += 1
                results.append(r)
            except FixError as e:
                results.append({"path": str(item.get("path", "")), "ok": False,
                                "action": str(item.get("action", "")),
                                "message": f"跳过：{e}", "backup": ""})
            except OSError as e:
                results.append({"path": str(item.get("path", "")), "ok": False,
                                "action": str(item.get("action", "")),
                                "message": f"文件操作失败：{e}", "backup": ""})
        return {"results": results, "ok": ok_count,
                "total": len(plan_items or []),
                "backups_dir": os.path.join(BACKUP_DIR_NAME, "")}
