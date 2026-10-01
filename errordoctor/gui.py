# -*- coding: utf-8 -*-
"""v2 桌面界面（tkinter，深色主题，零第三方依赖）。

结构：
  ├─ 工具栏：立即扫描 / 自动修复开关 / 设置 / 状态
  ├─ 左侧：检测到的问题列表
  └─ 右侧 Notebook：
       ├─ 报错详情（日志摘要 + AI 分析结果 + 修复计划勾选执行）
       ├─ AI 问答（内嵌对话窗口）
       └─ 历史与备份
自动流程：启动即扫描 → 发现报错自动分析 → 按设置弹“修改前确认”或全自动执行修复。

线程模型：engine 的 worker 线程只向 queue 投递事件；GUI 用 after() 轮询消费，
绝不从子线程直接操作控件。
"""

import os
import queue
import subprocess
import time
import tkinter as tk
from tkinter import ttk, messagebox

from . import config, engine as engine_mod, history

# ---------------------------------------------------------------- 主题

BG = "#14161f"          # 窗口底色
PANEL = "#1c1f2b"       # 面板
PANEL2 = "#232735"      # 次级面板
BORDER = "#2c3142"
FG = "#e8eaf2"
DIM = "#9aa2b8"
ACCENT = "#57c76f"
ACCENT_DARK = "#3fa35a"
RED = "#e05d5d"
YELLOW = "#e8b34b"
BLUE = "#5da8e0"
CODE = "#9fe3ae"

SEV_COLOR = {100: RED, 90: "#f0a06a", 80: YELLOW, 70: BLUE, 60: BLUE, 50: DIM}


def center(win: tk.Toplevel, w: int, h: int):
    win.update_idletasks()
    x = max(0, (win.winfo_screenwidth() - w) // 2)
    y = max(0, (win.winfo_screenheight() - h) // 3)
    win.geometry(f"{w}x{h}+{x}+{y}")


class App:
    def __init__(self, cfg: dict, force_args: dict = None):
        self.cfg = cfg
        self.force_args = force_args or {}
        self._quit_flag = False
        if self.force_args.get("autofix"):
            self.cfg["autofix"] = True
        if self.force_args.get("no_ask"):
            self.cfg["ask_before_fix"] = False

        self.q = queue.Queue()
        self.engine = engine_mod.Engine(cfg, on_event=self.q.put)
        self.issues = []
        self.analyses = {}          # sig -> 结果
        self.selected_sig = None
        self.plan_vars = []         # [(tk.BooleanVar, item, issue_title)]
        self.auto_round_sigs = set()
        self.chat_history = []      # [{"role","content"}]
        self._scan_after_id = None
        self._asked_settings = False
        self._started = time.time()

        self._build_root()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._build_settings_dialog()
        self._build_confirm_dialog()

        self.engine.start_watching()
        self.root.after(400, self._startup)
        self.root.after(150, self._poll)

    # ================================================================ 根窗口

    def _build_root(self):
        self.root = tk.Tk()
        self.root.title("TotemFix")
        self.root.configure(bg=BG)
        self.root.geometry("1100x720")
        self.root.minsize(920, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 8, 0, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                        padding=(16, 8), borderwidth=0, font=("", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL2)],
                  foreground=[("selected", ACCENT)])

        self.btn_cache = {}
        self._btn = self._make_button

    def _make_button(self, master, text, command=None, color=ACCENT,
                     big=False, danger=False):
        fg = "#0b120c" if color == ACCENT else (RED if danger else FG)
        bg = ACCENT if color == ACCENT else PANEL2
        active_bg = ACCENT_DARK if color == ACCENT else BORDER
        b = tk.Button(master, text=text, command=command, bg=bg, fg=fg,
                      activebackground=active_bg, activeforeground=fg,
                      relief="flat", bd=0, cursor="hand2",
                      padx=18 if big else 12, pady=8 if big else 5,
                      font=("", 11, "bold") if big else ("", 10),
                      highlightthickness=0)
        return b

    # ================================================================ 工具栏

    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=PANEL, height=52)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        self.btn_scan = self._btn(bar, "🔍 立即扫描", self._on_scan_click, big=True)
        self.btn_scan.pack(side="left", padx=(12, 6), pady=9)

        self.autofix_var = tk.BooleanVar(value=bool(self.cfg.get("autofix")))
        cb = tk.Checkbutton(bar, text="自动修复", variable=self.autofix_var,
                            command=self._on_autofix_toggle, bg=PANEL, fg=FG,
                            activebackground=PANEL, activeforeground=ACCENT,
                            selectcolor=PANEL2, font=("", 10),
                            highlightthickness=0, bd=0, cursor="hand2")
        cb.pack(side="left", padx=6)

        self.watch_label = tk.Label(bar, text="", bg=PANEL, fg=DIM, font=("", 9))
        self.watch_label.pack(side="left", padx=10)

        self.btn_settings = self._btn(bar, "⚙️ 设置", self._open_settings)
        self.btn_settings.pack(side="right", padx=(4, 12), pady=9)
        self.btn_chat = self._btn(bar, "💬 AI 问答", lambda: self._select_tab(1))
        self.btn_chat.pack(side="right", padx=4, pady=9)
        self.btn_hist = self._btn(bar, "🕘 历史与备份", lambda: self._select_tab(2))
        self.btn_hist.pack(side="right", padx=4, pady=9)

    def _on_autofix_toggle(self):
        self.cfg["autofix"] = bool(self.autofix_var.get())
        config.save(self.cfg)
        self._set_status(f"自动修复已{'开启' if self.cfg['autofix'] else '关闭'}")

    def _on_scan_click(self):
        self.btn_scan.config(state="disabled", text="⏳ 扫描中…")
        self.engine.scan("manual")

    def _on_close(self):
        if self.cfg.get("hide_on_close", True):
            self.root.iconify()
            self._set_status("已最小化到后台监控，出现新报错会自动弹出窗口")
        else:
            self._quit()

    def _quit(self):
        self._quit_flag = True
        self.engine.stop()
        for attr in ("_poll_id", "_topmost_id", "_scan_after_id"):
            aid = getattr(self, attr, None)
            if aid:
                try:
                    self.root.after_cancel(aid)
                except tk.TclError:
                    pass
        self.root.destroy()

    # ================================================================ 主体

    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        # ---- 左侧问题列表
        left = tk.Frame(body, bg=PANEL, width=340)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text=" 检测到的问题", bg=PANEL, fg=DIM,
                 font=("", 10, "bold"), anchor="w").pack(fill="x", pady=(10, 4))

        self.issue_list = tk.Listbox(left, bg=PANEL, fg=FG, bd=0,
                                     selectbackground=BORDER,
                                     selectforeground=FG,
                                     activestyle="none",
                                     highlightthickness=0,
                                     font=("", 10))
        self.issue_list.pack(fill="both", expand=True, padx=8)
        self.issue_list.bind("<<ListboxSelect>>", self._on_issue_select)
        self.issue_list.bind("<Double-Button-1>", lambda e: self._analyze_selected())
        self.issue_hint = tk.Label(left, bg=PANEL, fg=DIM, font=("", 9),
                                   text="尚未扫描", wraplength=300, justify="left")
        self.issue_hint.pack(fill="x", padx=10, pady=8)

        # ---- 右侧 Notebook
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(side="left", fill="both", expand=True)

        self.tab_detail = tk.Frame(self.notebook, bg=BG)
        self.tab_chat = tk.Frame(self.notebook, bg=BG)
        self.tab_history = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_detail, text=" 报错详情 ")
        self.notebook.add(self.tab_chat, text=" AI 问答 ")
        self.notebook.add(self.tab_history, text=" 历史与备份 ")

        self._build_detail_tab()
        self._build_chat_tab()
        self._build_history_tab()

    def _select_tab(self, idx):
        self.notebook.select(idx)

    # ------------------------------------------------ 详情页

    def _build_detail_tab(self):
        pad = tk.Frame(self.tab_detail, bg=BG)
        pad.pack(fill="both", expand=True, padx=12, pady=10)

        self.d_title = tk.Label(pad, text="选择左侧问题查看详情", bg=BG, fg=FG,
                                font=("", 13, "bold"), anchor="w", justify="left")
        self.d_title.pack(fill="x")
        self.d_meta = tk.Label(pad, text="", bg=BG, fg=DIM, font=("", 9),
                               anchor="w", justify="left")
        self.d_meta.pack(fill="x", pady=(2, 6))

        logframe = tk.Frame(pad, bg=BG)
        logframe.pack(fill="x")
        self.d_log = tk.Text(logframe, bg="#0d0e15", fg="#c8cddc", bd=0,
                             relief="flat", wrap="word", height=9,
                             font=("Consolas", 9), highlightthickness=0)
        log_scroll = tk.Scrollbar(logframe, command=self.d_log.yview, bg=PANEL2,
                                  activebackground=BORDER, relief="flat", bd=0)
        self.d_log.config(yscrollcommand=log_scroll.set)
        self.d_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        btns = tk.Frame(pad, bg=BG)
        btns.pack(fill="x", pady=8)
        self.btn_analyze = self._btn(btns, "🤖 AI 分析此报错", self._analyze_selected,
                                     color=ACCENT)
        self.btn_analyze.pack(side="left")
        self._btn(btns, "📂 打开文件位置", self._open_file).pack(side="left", padx=8)
        self._btn(btns, "📄 预览文件", self._preview_file).pack(side="left")
        self._btn(btns, "🤖 分析全部问题", self._analyze_all).pack(side="right")

        self.d_analysis = tk.Text(pad, bg=PANEL, fg=FG, bd=0, relief="flat",
                                  wrap="word", height=10,
                                  font=("", 10), highlightthickness=0)
        self.d_analysis.tag_configure("h", foreground=ACCENT, font=("", 10, "bold"))
        self.d_analysis.tag_configure("code", foreground=CODE, font=("Consolas", 9))
        self.d_analysis.tag_configure("dim", foreground=DIM, font=("", 9))
        self.d_analysis.tag_configure("err", foreground=RED)
        self.d_analysis.config(state="disabled")
        ascroll = tk.Scrollbar(pad, command=self.d_analysis.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.d_analysis.config(yscrollcommand=ascroll.set)
        self.d_analysis.pack(side="top", fill="both", expand=True)
        ascroll.pack(side="right", fill="y")

        # 修复计划区（动态重建）
        self.plan_frame = tk.Frame(pad, bg=BG)
        self.plan_frame.pack(fill="x")

        self.d_empty = tk.Label(pad, text="👈 扫描后从左侧选择一个报错，点击“AI 分析此报错”，\n"
                                          "AI 将给出出错原因、出错文件位置与解决办法。",
                                bg=BG, fg=DIM, font=("", 10), justify="left")
        self.d_empty.place(relx=0.5, rely=0.55, anchor="center")

    def _set_analysis_text(self, text, tags=None):
        self.d_analysis.config(state="normal")
        self.d_analysis.delete("1.0", "end")
        self.d_analysis.insert("1.0", text)
        if tags:
            for name, start, end in tags:
                self.d_analysis.tag_add(name, start, end)
        self.d_analysis.config(state="disabled")

    def _clear_plan_frame(self):
        for w in self.plan_frame.winfo_children():
            w.destroy()
        self.plan_vars = []

    def _render_plan(self, result):
        self._clear_plan_frame()
        plan = result.get("fix_plan") or []
        if not plan:
            tk.Label(self.plan_frame, text="AI 未生成自动修复计划，请按上方步骤手动操作。",
                     bg=BG, fg=DIM, font=("", 9)).pack(anchor="w", pady=(6, 2))
            return
        tk.Label(self.plan_frame, text="🛠 AI 修复计划（执行前自动备份，可在“历史与备份”页还原）：",
                 bg=BG, fg=FG, font=("", 10, "bold")).pack(anchor="w", pady=(8, 4))
        icons = {"delete": ("🗑 删除", RED), "rename": ("🔄 重命名", YELLOW),
                 "edit": ("✏️ 替换文本", BLUE), "write": ("📝 写入文件", ACCENT)}
        for item in plan:
            var = tk.BooleanVar(value=True)
            icon, color = icons.get(item.get("action"), ("修改", BLUE))
            row = tk.Frame(self.plan_frame, bg=PANEL2)
            row.pack(fill="x", pady=2)
            cb = tk.Checkbutton(row, variable=var, bg=PANEL2, fg=FG,
                                activebackground=PANEL2, selectcolor=PANEL,
                                highlightthickness=0, bd=0)
            cb.pack(side="left", padx=(8, 2))
            tk.Label(row, text=icon, bg=PANEL2, fg=color, font=("", 10, "bold")).pack(side="left")
            tk.Label(row, text=item.get("path", ""), bg=PANEL2, fg=CODE,
                     font=("Consolas", 10)).pack(side="left", padx=6)
            tk.Label(row, text=item.get("reason", ""), bg=PANEL2, fg=DIM,
                     font=("", 9)).pack(side="left", padx=6)
            self.plan_vars.append((var, item))
        bar = tk.Frame(self.plan_frame, bg=BG)
        bar.pack(fill="x", pady=(8, 4))
        self._btn(bar, "执行所选修复", self._apply_selected, color=ACCENT).pack(side="left")
        self._btn(bar, "全选", lambda: self._plan_check_all(True)).pack(side="left", padx=6)
        self._btn(bar, "全不选", lambda: self._plan_check_all(False)).pack(side="left")

    def _plan_check_all(self, on):
        for var, _ in self.plan_vars:
            var.set(on)

    def _apply_selected(self):
        items = [item for var, item in self.plan_vars if var.get()]
        if not items:
            messagebox.showinfo("提示", "请先勾选要执行的修复项。", parent=self.root)
            return
        if messagebox.askokcancel("确认执行", f"将执行 {len(items)} 项修复，执行前会自动备份原文件。\n继续？",
                                  parent=self.root):
            self.engine.apply(items, reason="manual")
            self._set_status(f"正在执行 {len(items)} 项修复…")

    # ------------------------------------------------ AI 问答页

    def _build_chat_tab(self):
        self.chat_text = tk.Text(self.tab_chat, bg=PANEL, fg=FG, bd=0, relief="flat",
                                 wrap="word", font=("", 10), highlightthickness=0)
        self.chat_text.tag_configure("user", foreground=BLUE, font=("", 10, "bold"))
        self.chat_text.tag_configure("ai", foreground=ACCENT)
        self.chat_text.tag_configure("sys", foreground=DIM, font=("", 9))
        self.chat_text.config(state="disabled")
        cscroll = tk.Scrollbar(self.tab_chat, command=self.chat_text.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.chat_text.config(yscrollcommand=cscroll.set)
        cscroll.pack(side="right", fill="y")
        self.chat_text.pack(side="top", fill="both", expand=True, padx=(12, 0), pady=(10, 4))

        bar = tk.Frame(self.tab_chat, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        self.chat_input = tk.Entry(bar, bg=PANEL2, fg=FG, insertbackground=FG,
                                   relief="flat", bd=0, font=("", 11))
        self.chat_input.pack(side="left", fill="x", expand=True, ipady=7)
        self.chat_input.bind("<Return>", lambda e: self._send_chat())
        self._btn(bar, "发送 ⏎", self._send_chat, color=ACCENT).pack(side="left", padx=(8, 0))

        self._chat_append("sys", "你好！我是内嵌的 Minecraft 故障排查助手（DeepSeek 驱动）。\n"
                                 "可以把报错日志粘贴给我，或直接描述问题，例如：“进游戏就闪退怎么办？”\n"
                                 "（AI 问答与自动修复互不影响，可在设置中更换模型。）")

    def _chat_append(self, role, text):
        self.chat_text.config(state="normal")
        tag = {"user": "user", "ai": "ai", "sys": "sys"}.get(role, "ai")
        label = {"user": "你：", "ai": "AI：", "sys": ""}.get(role, "")
        self.chat_text.insert("end", f"{label}{text}\n\n", tag)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _send_chat(self):
        q = self.chat_input.get().strip()
        if not q:
            return
        self.chat_input.delete(0, "end")
        self.chat_history.append({"role": "user", "content": q})
        self._chat_append("user", q)
        self._chat_append("sys", "AI 思考中…")
        self.engine.chat(self.chat_history, q)

    # ------------------------------------------------ 历史与备份页

    def _build_history_tab(self):
        tk.Label(self.tab_history, text="操作历史", bg=BG, fg=DIM,
                 font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        hist_frame = tk.Frame(self.tab_history, bg=BG)
        hist_frame.pack(fill="x", padx=12)
        self.hist_list = tk.Listbox(hist_frame, bg=PANEL, fg=FG, bd=0, height=8,
                                    selectbackground=BORDER, selectforeground=FG,
                                    activestyle="none", highlightthickness=0,
                                    font=("", 9))
        hscroll = tk.Scrollbar(hist_frame, command=self.hist_list.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.hist_list.config(yscrollcommand=hscroll.set)
        self.hist_list.pack(side="left", fill="both", expand=True)
        hscroll.pack(side="right", fill="y")

        tk.Label(self.tab_history, text="修复备份（.minecraft/errordoctor-backups）",
                 bg=BG, fg=DIM, font=("", 10, "bold")).pack(anchor="w", padx=12,
                                                            pady=(10, 2))
        bk_frame = tk.Frame(self.tab_history, bg=BG)
        bk_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.backup_list = tk.Listbox(bk_frame, bg=PANEL, fg=FG, bd=0,
                                      selectbackground=BORDER, selectforeground=FG,
                                      activestyle="none", highlightthickness=0,
                                      font=("", 9))
        bscroll = tk.Scrollbar(bk_frame, command=self.backup_list.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.backup_list.config(yscrollcommand=bscroll.set)
        self.backup_list.pack(side="left", fill="both", expand=True)
        bscroll.pack(side="right", fill="y")

        bar = tk.Frame(self.tab_history, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        self._btn(bar, "还原所选备份", self._restore_backup).pack(side="left")
        self._btn(bar, "刷新", self._refresh_history).pack(side="left", padx=8)
        self._btn(bar, "清空历史", self._clear_history, danger=True).pack(side="right")

        self._refresh_history()

    def _refresh_history(self):
        self.hist_list.delete(0, "end")
        for r in history.list_records(100):
            kind = {"scan": "扫描", "analyze": "分析", "fix": "修复",
                    "restore": "还原", "config": "系统", "chat": "问答"}.get(r["kind"], r["kind"])
            self.hist_list.insert("end", f"[{r['time']}] [{kind}] {r['summary']}")
        self.backup_list.delete(0, "end")
        self.backup_items = []
        for b in self.engine.list_backups():
            self.backup_items.append(b)
            self.backup_list.insert("end", f"[{b['time']}] {b['orig']}  ({b['size']//1024}KB)")

    def _restore_backup(self):
        sel = self.backup_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一份备份。", parent=self.root)
            return
        b = self.backup_items[sel[0]]
        if not messagebox.askokcancel("还原备份",
                                      f"用备份覆盖当前文件：{b['orig']}\n继续？",
                                      parent=self.root):
            return
        try:
            orig = self.engine.restore(b["backup"])
            history.add("restore", f"已从备份还原 {orig}")
            self._set_status(f"已还原：{orig}")
            self._refresh_history()
        except Exception as e:
            messagebox.showerror("还原失败", str(e), parent=self.root)

    def _clear_history(self):
        if messagebox.askokcancel("清空历史", "确定清空全部操作历史？", parent=self.root):
            history.clear()
            self._refresh_history()

    # ================================================================ 状态栏

    def _build_statusbar(self):
        self.status = tk.Label(self.root, text="启动中…", bg=PANEL, fg=DIM,
                               anchor="w", font=("", 9), padx=10, pady=4)
        self.status.pack(side="bottom", fill="x")

    def _set_status(self, text):
        try:
            self.status.config(text=text)
        except tk.TclError:
            pass

    # ================================================================ 设置弹窗

    def _build_settings_dialog(self):
        self.settings_win = None
        self.sv = {}

    def _open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            return
        w = tk.Toplevel(self.root, bg=PANEL)
        w.title("设置")
        w.transient(self.root)
        center(w, 560, 640)
        self.settings_win = w

        sv = self.sv
        rows = [
            ("pcl", "PCL 启动器文件夹（含 PCL.exe）", self.cfg.get("pcl_dir", "")),
            ("mc", "Minecraft 文件夹（.minecraft）", self.cfg.get("mc_dir", "")),
            ("key", "DeepSeek API Key（sk-...，仅保存在本机）", ""),
            ("base", "API 地址（OpenAI 兼容，默认官方）", self.cfg.get("api_base", "")),
            ("model", "模型", self.cfg.get("model", "")),
        ]
        for name, label, value in rows:
            tk.Label(w, text=label, bg=PANEL, fg=DIM, font=("", 9)).pack(
                anchor="w", padx=16, pady=(10, 2))
            e = tk.Entry(w, bg=PANEL2, fg=FG, insertbackground=FG, relief="flat",
                         bd=0, font=("Consolas", 10))
            e.pack(fill="x", padx=16, ipady=6)
            if name == "key":
                e.config(show="*")
                if self.cfg.get("api_key"):
                    e.insert(0, "")
                    e.config(state="normal")
                    tk.Label(w, text="（已配置，留空表示保持不变）", bg=PANEL, fg=DIM,
                             font=("", 8)).pack(anchor="w", padx=16)
            else:
                e.insert(0, value)
            sv[name] = e

        det = tk.Frame(w, bg=PANEL)
        det.pack(fill="x", padx=16, pady=(10, 0))
        self._btn(det, "🔎 自动检测 PCL 路径", lambda: self._autodetect("pcl")).pack(side="left")
        self._btn(det, "🔎 自动检测 MC 路径", lambda: self._autodetect("mc")).pack(side="left", padx=8)

        self.sv["ask"] = tk.BooleanVar(value=bool(self.cfg.get("ask_before_fix", True)))
        self.sv["auto_analyze"] = tk.BooleanVar(value=bool(self.cfg.get("auto_analyze", True)))
        self.sv["hide"] = tk.BooleanVar(value=bool(self.cfg.get("hide_on_close", True)))
        self.sv["interval"] = tk.StringVar(value=str(self.cfg.get("watch_interval", 3)))

        tk.Checkbutton(w, text="修改文件前弹窗确认（弹窗内可勾选“下次不再询问”）",
                       variable=self.sv["ask"], bg=PANEL, fg=FG,
                       activebackground=PANEL, selectcolor=PANEL2,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Checkbutton(w, text="发现报错后自动调用 AI 分析",
                       variable=self.sv["auto_analyze"], bg=PANEL, fg=FG,
                       activebackground=PANEL, selectcolor=PANEL2,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=16)
        tk.Checkbutton(w, text="点关闭按钮时最小化后台监控（取消勾选=直接退出）",
                       variable=self.sv["hide"], bg=PANEL, fg=FG,
                       activebackground=PANEL, selectcolor=PANEL2,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=16)

        int_row = tk.Frame(w, bg=PANEL)
        int_row.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(int_row, text="后台监控间隔（秒）：", bg=PANEL, fg=DIM,
                 font=("", 9)).pack(side="left")
        tk.Spinbox(int_row, from_=1, to=60, textvariable=self.sv["interval"], width=5,
                   bg=PANEL2, fg=FG, bd=0, relief="flat",
                   buttonbackground=PANEL2).pack(side="left")

        bar = tk.Frame(w, bg=PANEL)
        bar.pack(fill="x", padx=16, pady=16)
        self._btn(bar, "💾 保存", self._save_settings, color=ACCENT, big=True).pack(side="left")
        self._btn(bar, "🔌 测试连接", self._test_connection).pack(side="left", padx=8)
        self._btn(bar, "退出程序", self._quit, danger=True).pack(side="right")

    def _autodetect(self, which):
        if which == "pcl":
            p = config.detect_pcl_dir()
            if p:
                self.sv["pcl"].delete(0, "end")
                self.sv["pcl"].insert(0, p)
            else:
                messagebox.showinfo("自动检测", "未检测到 PCL 文件夹，请手动填写。",
                                    parent=self.root)
        else:
            p = config.detect_mc_dir(self.sv["pcl"].get().strip())
            if p:
                self.sv["mc"].delete(0, "end")
                self.sv["mc"].insert(0, p)
            else:
                messagebox.showinfo("自动检测", "未检测到 .minecraft 文件夹，请手动填写。",
                                    parent=self.root)

    def _save_settings(self):
        self.cfg["pcl_dir"] = self.sv["pcl"].get().strip()
        self.cfg["mc_dir"] = self.sv["mc"].get().strip()
        key = self.sv["key"].get().strip()
        if key and not key.startswith("sk-****"):
            self.cfg["api_key"] = key
        self.cfg["api_base"] = self.sv["base"].get().strip() or config.DEFAULTS["api_base"]
        self.cfg["model"] = self.sv["model"].get().strip() or "deepseek-chat"
        self.cfg["ask_before_fix"] = bool(self.sv["ask"].get())
        self.cfg["auto_analyze"] = bool(self.sv["auto_analyze"].get())
        self.cfg["hide_on_close"] = bool(self.sv["hide"].get())
        try:
            self.cfg["watch_interval"] = max(1, min(60, int(self.sv["interval"].get())))
        except ValueError:
            self.cfg["watch_interval"] = 3
        self.autofix_var.set(bool(self.cfg["autofix"]))
        config.save(self.cfg)
        history.add("config", "已更新设置")
        self._update_watch_label()
        self._set_status("设置已保存")
        if self.settings_win:
            self.settings_win.destroy()
            self.settings_win = None

    def _test_connection(self):
        self._set_status("正在测试 DeepSeek 连接…")
        key = self.sv["key"].get().strip()
        base = self.sv["base"].get().strip()
        model = self.sv["model"].get().strip()

        def worker():
            from . import deepseek
            use_key = key if (key and not key.startswith("sk-****")) else self.cfg.get("api_key", "")
            try:
                client = deepseek.DeepSeekClient(base or "https://api.deepseek.com",
                                                 use_key, model or "deepseek-chat",
                                                 timeout=60)
                resp = client._post({"model": client.model,
                                     "messages": [{"role": "user",
                                                   "content": "回复“连接成功”四个字"}],
                                     "max_tokens": 20, "stream": False})
                try:
                    reply = resp["choices"][0]["message"]["content"]
                except Exception:
                    reply = ""
                self.q.put({"type": "conn_test", "ok": True,
                            "reply": reply or "（接口响应正常）"})
            except deepseek.DeepSeekError as e:
                self.q.put({"type": "conn_test", "ok": False, "error": str(e)})

        import threading
        threading.Thread(target=worker, daemon=True).start()

    # ================================================================ 确认弹窗

    def _build_confirm_dialog(self):
        self.confirm_win = None

    def _show_fix_confirm(self, plans):
        """plans: [(issue_title, fix_item)]，返回后按用户选择执行。"""
        if self.confirm_win and self.confirm_win.winfo_exists():
            self.confirm_win.destroy()
        w = tk.Toplevel(self.root, bg=PANEL)
        w.title("确认 AI 修复")
        w.transient(self.root)
        w.attributes("-topmost", True)
        center(w, 620, 460)
        self.confirm_win = w

        tk.Label(w, text="🤖 AI 已分析完成，将执行以下修复：", bg=PANEL, fg=FG,
                 font=("", 12, "bold")).pack(anchor="w", padx=16, pady=(14, 6))

        box = tk.Text(w, bg=PANEL2, fg=FG, relief="flat", bd=0, wrap="word",
                      height=12, font=("", 10), highlightthickness=0)
        box.pack(fill="both", expand=True, padx=16)
        self.confirm_text = box
        icons = {"delete": "🗑 删除", "rename": "🔄 重命名", "edit": "✏️ 替换文本",
                 "write": "📝 写入文件"}
        for title, item in plans:
            box.insert("end", f"· {icons.get(item.get('action'), '修改')} "
                              f"{item.get('path', '')}\n", "code")
            if item.get("reason"):
                box.insert("end", f"   {item['reason']}\n", "dim")
        box.tag_configure("code", foreground=CODE, font=("Consolas", 10))
        box.tag_configure("dim", foreground=DIM, font=("", 9))
        box.config(state="disabled")

        tk.Label(w, text="⚠️ 执行前会自动备份原文件到 .minecraft/errordoctor-backups/，"
                         "可随时在“历史与备份”页还原。",
                 bg=PANEL, fg=YELLOW, font=("", 9), wraplength=580,
                 justify="left").pack(anchor="w", padx=16, pady=6)

        self.confirm_noask = tk.BooleanVar(value=False)
        tk.Checkbutton(w, text="✔ 下次不再询问，直接自动执行（可在设置中改回）",
                       variable=self.confirm_noask, bg=PANEL, fg=FG,
                       activebackground=PANEL, selectcolor=PANEL2,
                       highlightthickness=0, bd=0,
                       font=("", 10)).pack(anchor="w", padx=16, pady=(4, 8))

        bar = tk.Frame(w, bg=PANEL)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        self._btn(bar, "✅ 执行修复", lambda: self._confirm_result(True),
                  color=ACCENT, big=True).pack(side="left")
        self._btn(bar, "取消（只看不改）", lambda: self._confirm_result(False),
                  danger=True).pack(side="left", padx=10)

        w.protocol("WM_DELETE_WINDOW", lambda: self._confirm_result(False))
        w.grab_set()
        self._pending_confirm_plans = plans
        self._confirm_done = False

    def _confirm_result(self, ok):
        if getattr(self, "_confirm_done", True):
            return
        self._confirm_done = True
        no_ask = bool(self.confirm_noask.get())
        plans = getattr(self, "_pending_confirm_plans", [])
        win = self.confirm_win
        self.confirm_win = None
        if win:
            try:
                win.grab_release()
                win.destroy()
            except tk.TclError:
                pass
        if ok and plans:
            if no_ask:
                self.cfg["ask_before_fix"] = False
                config.save(self.cfg)
                self._set_status("已勾选“下次不再询问”，此后将自动执行修复（可在设置中改回）")
            items = [item for _, item in plans]
            history.add("fix", f"确认执行 AI 修复 {len(items)} 项")
            self.engine.apply(items, reason="auto")
            self._set_status(f"正在执行 {len(items)} 项修复…")
        else:
            self._set_status("已取消自动修复，可在“报错详情”页手动勾选执行")

    # ================================================================ 启动

    def _startup(self):
        # 自动识别路径
        if not self.cfg.get("pcl_dir"):
            self.cfg["pcl_dir"] = config.detect_pcl_dir()
        if not self.cfg.get("mc_dir"):
            self.cfg["mc_dir"] = config.detect_mc_dir(self.cfg.get("pcl_dir"))
        config.save(self.cfg)
        self._update_watch_label()
        self._set_status("启动自动扫描中…")
        self.engine.scan("startup")

    def _update_watch_label(self):
        key_set = bool(self.cfg.get("api_key"))
        self.watch_label.config(
            text=f"🟢 监控中 · DeepSeek {'已配置' if key_set else '未配置'}"
                 f" · 询问{'开' if self.cfg.get('ask_before_fix', True) else '关'}"
                 f" · {self.cfg.get('watch_interval', 3)}s/次")

    # ================================================================ 事件轮询

    def _poll(self):
        if self._quit_flag:
            return
        try:
            while True:
                ev = self.q.get_nowait()
                self._dispatch(ev)
        except queue.Empty:
            pass
        try:
            self._poll_id = self.root.after(150, self._poll)
        except tk.TclError:
            pass

    def _dispatch(self, ev):
        t = ev.get("type")
        if t == "fs_change":
            if self._scan_after_id:
                self.root.after_cancel(self._scan_after_id)
            self._scan_after_id = self.root.after(1200,
                                                  lambda: self._trigger_watch_scan())
        elif t == "scan_done":
            self._on_scan_done(ev)
        elif t == "scan_error":
            self._set_status(f"扫描失败：{ev['error']}")
        elif t == "new_issues":
            self._on_new_issues(ev["issues"])
        elif t == "analyzing":
            self._set_status(f"🤖 正在分析：{ev['title'][:60]}")
        elif t == "analyzed":
            self._on_analyzed(ev)
        elif t == "analyze_done":
            self._on_analyze_done(ev)
        elif t == "applied":
            self._on_applied(ev)
        elif t == "chat_reply":
            self._on_chat_reply(ev)
        elif t == "conn_test":
            self._on_conn_test(ev)

    def _trigger_watch_scan(self):
        self._scan_after_id = None
        self.engine.scan("watch")

    def _on_scan_done(self, ev):
        self.btn_scan.config(state="normal", text="🔍 立即扫描")
        res = ev["result"]
        self.issues = res["issues"]
        self._render_issue_list()
        stats = res["stats"]
        self._set_status(f"扫描完成（{ev['reason']}）：发现 {stats['issue_count']} 个问题 · "
                         f"{stats['scanned_at']}")
        if ev["reason"] in ("startup", "manual", "watch"):
            history.add("scan",
                        f"扫描完成，发现 {stats['issue_count']} 个问题"
                        + ("" if not res["errors"] else "（存在路径未配置提示）"))
        if res["errors"] and ev["reason"] in ("startup", "manual"):
            self.issue_hint.config(text="提示：" + "；".join(res["errors"]))

    def _render_issue_list(self):
        self.issue_list.delete(0, "end")
        self._issue_index = {}
        for n, it in enumerate(self.issues):
            color = SEV_COLOR.get(it["severity"], DIM)
            label = f"【{it['severity_text']}】{it['title'][:70]}"
            self.issue_list.insert("end", label)
            self.issue_list.itemconfig(n, fg=color)
            self._issue_index[n] = it
        if not self.issues:
            self.issue_hint.config(text="🎉 未发现明显报错。后台持续监控中，"
                                        "出现新报错会自动弹出分析窗口。")
            self._select_issue(None)
        else:
            self.issue_hint.config(text=f"共 {len(self.issues)} 个问题，双击可直接 AI 分析")

    def _on_issue_select(self, _ev=None):
        sel = self.issue_list.curselection()
        if sel:
            self._select_issue(self._issue_index.get(sel[0]))

    def _select_issue(self, issue):
        self.selected_sig = issue["sig"] if issue else None
        if issue is None:
            self.d_empty.lift()
            self.d_title.config(text="选择左侧问题查看详情")
            self.d_meta.config(text="")
            self.d_log.config(state="normal")
            self.d_log.delete("1.0", "end")
            self.d_log.config(state="disabled")
            self._set_analysis_text("")
            self._clear_plan_frame()
            return
        self.d_empty.lower()
        self.d_title.config(text=f"{issue['kind']} · {issue['title']}")
        self.d_meta.config(text=f"{issue['source']}（第 {issue['line']} 行）\n{issue['path']}")
        self.d_log.config(state="normal")
        self.d_log.delete("1.0", "end")
        self.d_log.insert("1.0", issue["excerpt"])
        self.d_log.config(state="disabled")
        rec = self.analyses.get(issue["sig"])
        if rec:
            self._render_analysis(rec)
        else:
            self._set_analysis_text("尚未分析。点击上方“🤖 AI 分析此报错”。")
            self._clear_plan_frame()

    def _current_issue(self):
        if not self.selected_sig:
            return None
        return next((i for i in self.issues if i["sig"] == self.selected_sig), None)

    def _analyze_selected(self):
        issue = self._current_issue()
        if issue is None:
            messagebox.showinfo("提示", "请先在左侧选择一个报错问题。", parent=self.root)
            return
        if not self.cfg.get("api_key"):
            self._ask_settings("需要先配置 DeepSeek API Key 才能进行 AI 分析。")
            return
        self.btn_analyze.config(state="disabled", text="⏳ 分析中…")
        self._set_analysis_text("AI 分析中，请稍候（约 10~60 秒）…")
        self.engine.analyze([issue])

    def _analyze_all(self):
        if not self.issues:
            messagebox.showinfo("提示", "当前没有检测到问题。", parent=self.root)
            return
        if not self.cfg.get("api_key"):
            self._ask_settings("需要先配置 DeepSeek API Key 才能进行 AI 分析。")
            return
        self._set_status(f"开始分析全部 {len(self.issues)} 个问题…")
        self.engine.analyze(self.issues, limit=len(self.issues))

    def _on_analyzed(self, ev):
        sig = ev.get("sig")
        if ev.get("result"):
            self.analyses[sig] = ev["result"]
        else:
            self.analyses[sig] = {"issue_sig": sig, "error": ev.get("error", "分析失败")}
        if sig == self.selected_sig:
            self._render_analysis(self.analyses[sig])
        self.btn_analyze.config(state="normal", text="🤖 AI 分析此报错")

    def _render_analysis(self, rec):
        if rec.get("error"):
            self._set_analysis_text(f"❌ {rec['error']}", tags=[("err", "1.0", "end")])
            self._clear_plan_frame()
            return
        conf = {"high": "高", "medium": "中", "low": "低"}.get(rec.get("confidence"), "中")
        lines = []
        lines.append("📋 总结\n" + (rec.get("summary") or "（无）"))
        lines.append("\n\n🔍 出错原因\n" + (rec.get("cause") or "（无）"))
        ef = rec.get("error_file") or ""
        lines.append("\n\n📁 出错文件位置\n" + (ef if ef else "（AI 未能定位到具体文件）"))
        steps = rec.get("solution_steps") or []
        if steps:
            lines.append("\n\n✅ 解决办法")
            for i, s in enumerate(steps, 1):
                lines.append(f"\n{i}. {s}")
        lines.append(f"\n\n🎯 置信度：{conf}")
        text = "".join(lines)
        self._set_analysis_text(text, tags=[("h", "1.0", "1.end")])
        self._render_plan(rec)

    def _on_analyze_done(self, ev):
        self._set_status(f"AI 分析完成：成功 {ev['ok_count']} / 失败 {ev['fail_count']}")
        history.add("analyze", f"AI 分析完成：成功 {ev['ok_count']}，失败 {ev['fail_count']}")
        if not self.auto_round_sigs:
            return
        sigs = self.auto_round_sigs
        self.auto_round_sigs = set()
        plans = []
        for sig in sigs:
            rec = self.analyses.get(sig) or {}
            for item in rec.get("fix_plan") or []:
                plans.append((rec.get("issue_title", sig), item))
        if not plans:
            return
        if not self.cfg.get("autofix", True):
            self._set_status("自动修复已关闭，可在“报错详情”页手动执行修复计划")
            return
        if self.cfg.get("ask_before_fix", True):
            self._show_fix_confirm(plans)
        else:
            items = [item for _, item in plans]
            history.add("fix", f"按设置自动执行 AI 修复 {len(items)} 项")
            self.engine.apply(items, reason="auto")
            self._set_status(f"按设置全自动执行 {len(items)} 项修复…")

    def _on_applied(self, ev):
        if ev.get("error"):
            messagebox.showerror("修复失败", ev["error"], parent=self.root)
            self._set_status("修复失败：" + ev["error"])
            return
        res = ev["result"]
        ok = res["ok"]
        detail = "；".join(f"{r['path']}: {'成功' if r['ok'] else r['message']}"
                           for r in res["results"])
        history.add("fix", f"AI 修复完成 {ok}/{res['total']} 项", detail)
        self._set_status(f"修复完成 {ok}/{res['total']} 项（已自动备份，可还原）")
        messagebox.showinfo("修复完成",
                            f"已执行 {ok}/{res['total']} 项修复：\n\n{detail}\n\n"
                            "所有修改前均已自动备份，可在“历史与备份”页还原。",
                            parent=self.root)
        self.engine.scan("after_fix")

    def _on_new_issues(self, issues):
        self._render_issue_list()
        n = len(issues)
        self._popup_window(f"⚠️ 检测到 {n} 个新报错！")
        self._set_status(f"发现 {n} 个新报错，开始自动处理…")
        history.add("scan", f"检测到 {n} 个新报错",
                    "；".join(i["title"][:60] for i in issues))
        # 自动选中第一个新问题
        self.issue_list.selection_clear(0, "end")
        for idx, it in self._issue_index.items():
            if it["sig"] == issues[0]["sig"]:
                self.issue_list.selection_set(idx)
                self.issue_list.see(idx)
                break
        self._select_issue(issues[0])
        self._select_tab(0)

        if not self.cfg.get("api_key"):
            self._ask_settings(f"检测到 {n} 个报错，但尚未配置 DeepSeek API Key，"
                               "无法自动分析。")
            return
        if self.cfg.get("auto_analyze", True):
            self.auto_round_sigs = {i["sig"] for i in issues}
            self.engine.analyze(issues)
        else:
            self._set_status("已关闭自动 AI 分析，可在报错详情页手动分析")

    def _popup_window(self, msg):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._topmost_id = self.root.after(900, self._clear_topmost)
        try:
            self.root.bell()
        except tk.TclError:
            pass
        self.issue_hint.config(text=msg)

    def _clear_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _on_chat_reply(self, ev):
        if ev.get("error"):
            self._chat_append("sys", f"❌ {ev['error']}")
        else:
            self._chat_append("ai", ev["reply"])
            self.chat_history.append({"role": "assistant", "content": ev["reply"]})

    def _on_conn_test(self, ev):
        if ev.get("ok"):
            messagebox.showinfo("连接成功", "DeepSeek 连接正常！\n回复：" + ev.get("reply", ""),
                                parent=self.root)
        else:
            messagebox.showerror("连接失败", ev.get("error", ""), parent=self.root)
        self._set_status("DeepSeek 连接测试完成")

    def _ask_settings(self, msg):
        if messagebox.askokcancel("需要配置", msg + "\n\n现在打开设置？", parent=self.root):
            self._open_settings()

    # ------------------------------------------------ 文件操作

    def _current_file_path(self):
        issue = self._current_issue()
        if not issue:
            messagebox.showinfo("提示", "请先在左侧选择一个报错问题。", parent=self.root)
            return None
        p = issue.get("path", "")
        if not p or not os.path.isfile(p):
            messagebox.showinfo("提示", "该报错对应的文件已不存在。", parent=self.root)
            return None
        return p

    def _open_file(self):
        p = self._current_file_path()
        if not p:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", p])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(p)])
        except OSError as e:
            messagebox.showerror("打开失败", str(e), parent=self.root)

    def _preview_file(self):
        p = self._current_file_path()
        if not p:
            return
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                raw = f.read(min(size, 200 * 1024))
            text = raw.decode("utf-8", errors="replace")
            w = tk.Toplevel(self.root, bg=PANEL)
            w.title("文件预览 - " + os.path.basename(p))
            w.transient(self.root)
            center(w, 760, 480)
            t = tk.Text(w, bg="#0d0e15", fg="#c8cddc", relief="flat", bd=0,
                        wrap="none", font=("Consolas", 9), highlightthickness=0)
            t.insert("1.0", text + ("\n\n…（仅显示前 200KB）" if size > 200 * 1024 else ""))
            t.config(state="disabled")
            t.pack(fill="both", expand=True, padx=10, pady=10)
        except OSError as e:
            messagebox.showerror("预览失败", str(e), parent=self.root)

    # ================================================================ 主循环

    def run(self):
        self.root.mainloop()


def launch(cfg: dict, force_args: dict = None):
    app = App(cfg, force_args)
    app.run()
