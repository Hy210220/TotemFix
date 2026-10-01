# -*- coding: utf-8 -*-
"""自动流程引擎：扫描 → 差异检测 → AI 分析 → 修复执行，全部在后台线程完成，
通过事件回调把结果交给 GUI（GUI 用队列/after 消费事件，保持界面不卡顿）。"""

import os
import threading

from . import config, deepseek, fixer, scanner, watcher


def _default_client_factory(cfg: dict):
    return deepseek.DeepSeekClient(cfg.get("api_base", ""),
                                   cfg.get("api_key", ""),
                                   cfg.get("model", "deepseek-chat"))


class Engine:
    def __init__(self, cfg: dict, on_event=None, client_factory=None,
                 max_auto_analyze: int = 3):
        self.cfg = cfg                                # GUI 与引擎共享同一 dict
        self.on_event = on_event or (lambda ev: None)
        self.client_factory = client_factory or _default_client_factory
        self.max_auto_analyze = max_auto_analyze

        self.issues = []          # 最近一次扫描结果
        self.analyses = {}        # sig -> 分析结果（含 {"error": ...} 失败记录）
        self._sigs = set()        # 已见过的报错签名（用于识别“新报错”）
        self._scan_lock = threading.Lock()
        self._running = True
        self.watcher = None
        self.pcl_watcher = None

    # ------------------------------------------------------------ 事件

    def _emit(self, ev: dict):
        try:
            self.on_event(ev)
        except Exception:
            pass

    # ------------------------------------------------------------ 监控

    def start_watching(self):
        if self.watcher is None and self._running:
            self.watcher = watcher.Watcher(self.cfg, self._on_fs_change)
            self.watcher.start()
        # PCL 文件夹尚未定位时，启动 PCL2 进程监控：用户打开 PCL2 后自动定位
        if self.pcl_watcher is None and self._running \
                and not config._is_pcl_dir(self.cfg.get("pcl_dir", "")):
            self.pcl_watcher = watcher.PclProcessWatcher(
                self.cfg, self._on_pcl_found)
            self.pcl_watcher.start()

    def stop(self):
        self._running = False
        if self.watcher:
            self.watcher.stop()
        if self.pcl_watcher:
            self.pcl_watcher.stop()

    def _on_fs_change(self):
        self._emit({"type": "fs_change"})

    def _on_pcl_found(self, exe_path: str):
        self._emit({"type": "pcl_detected",
                    "dir": os.path.dirname(os.path.abspath(exe_path))})

    # ------------------------------------------------------------ 扫描

    def scan(self, reason: str = "manual"):
        threading.Thread(target=self._scan_worker, args=(reason,),
                         daemon=True, name="scan").start()

    def _scan_worker(self, reason: str):
        with self._scan_lock:
            try:
                res = scanner.scan(self.cfg)
            except Exception as e:
                self._emit({"type": "scan_error", "error": str(e)})
                return
            issues = res["issues"]
            new = [i for i in issues if i["sig"] not in self._sigs]
            self._sigs = {i["sig"] for i in issues}
            self.issues = issues
            self._emit({"type": "scan_done", "reason": reason, "result": res})
            if new:
                self._emit({"type": "new_issues", "issues": new})

    # ------------------------------------------------------------ AI 分析

    def analyze(self, issues: list, limit: int = None):
        threading.Thread(target=self._analyze_worker, args=(issues, limit),
                         daemon=True, name="analyze").start()

    def _analyze_worker(self, issues: list, limit: int = None):
        client = self.client_factory(self.cfg)
        ok_count = fail_count = 0
        cap = limit if limit is not None else self.max_auto_analyze
        meta = {"pcl_dir": self.cfg.get("pcl_dir", ""),
                "mc_dir": self.cfg.get("mc_dir", "")}
        for it in list(issues)[:cap]:
            self._emit({"type": "analyzing", "sig": it["sig"],
                        "title": it["title"]})
            try:
                result = client.analyze(it["excerpt"], meta)
                result["issue_sig"] = it["sig"]
                result["issue_title"] = it["title"]
                self.analyses[it["sig"]] = result
                self._emit({"type": "analyzed", "sig": it["sig"], "result": result})
                ok_count += 1
            except deepseek.DeepSeekError as e:
                rec = {"issue_sig": it["sig"], "issue_title": it["title"],
                       "error": str(e)}
                self.analyses[it["sig"]] = rec
                self._emit({"type": "analyzed", "sig": it["sig"], "error": str(e),
                            "title": it["title"]})
                fail_count += 1
            except Exception as e:  # 网络等意外错误不中断后续分析
                rec = {"issue_sig": it["sig"], "issue_title": it["title"],
                       "error": f"分析失败：{e}"}
                self.analyses[it["sig"]] = rec
                self._emit({"type": "analyzed", "sig": it["sig"],
                            "error": str(e), "title": it["title"]})
                fail_count += 1
        self._emit({"type": "analyze_done", "ok_count": ok_count,
                    "fail_count": fail_count})

    # ------------------------------------------------------------ 修复

    def apply(self, plan: list, reason: str = "ai"):
        threading.Thread(target=self._apply_worker, args=(plan, reason),
                         daemon=True, name="apply").start()

    def _apply_worker(self, plan: list, reason: str):
        try:
            ex = fixer.FixExecutor(self.cfg.get("mc_dir", ""))
            outcome = ex.execute(plan)
        except fixer.FixError as e:
            self._emit({"type": "applied", "error": str(e)})
            return
        except Exception as e:
            self._emit({"type": "applied", "error": f"修复执行失败：{e}"})
            return
        self._emit({"type": "applied", "result": outcome, "reason": reason})

    # ------------------------------------------------------------ 备份

    def list_backups(self):
        try:
            return fixer.FixExecutor(self.cfg.get("mc_dir", "")).list_backups()
        except fixer.FixError:
            return []

    def restore(self, backup: str):
        try:
            return fixer.FixExecutor(self.cfg.get("mc_dir", "")).restore(backup)
        except fixer.FixError as e:
            raise

    # ------------------------------------------------------------ AI 问答

    def chat(self, history: list, question: str):
        threading.Thread(target=self._chat_worker, args=(history, question),
                         daemon=True, name="chat").start()

    def _chat_worker(self, history, question):
        try:
            client = self.client_factory(self.cfg)
            reply = client.chat(history, question)
            self._emit({"type": "chat_reply", "reply": reply})
        except deepseek.DeepSeekError as e:
            self._emit({"type": "chat_reply", "error": str(e)})
        except Exception as e:
            self._emit({"type": "chat_reply", "error": f"请求失败：{e}"})
