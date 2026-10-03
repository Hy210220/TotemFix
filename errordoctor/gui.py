# -*- coding: utf-8 -*-
"""v2.2 桌面界面（tkinter，PCL2 设计语言：卡片式布局 + 主题蓝强调色 +
悬停反馈 + 分区标题；蓝白简约配色，零第三方依赖）。

界面结构：
  ├─ 顶栏：Logo + 状态 + 立即扫描 / 自动修复开关 / 设置（带悬停提示）
  ├─ 左侧：检测到的问题卡片列表（严重度色条 / 类型徽章 / 悬停高亮）
  └─ 右侧 Notebook：
       ├─ 报错详情（日志摘要 + AI 分析分区卡片 + 修复计划勾选执行）
       ├─ AI 问答（左右气泡对话）
       └─ 历史与备份
  └─ 右下角浮动通知（toast）
自动流程：启动即扫描 → 发现报错自动分析 → 按设置弹“修改前确认”或全自动执行修复；
后台监控 PCL2 进程（用户打开 PCL2 时自动定位其文件夹）与日志变化。

线程模型：engine 的 worker 线程只向 queue 投递事件；GUI 用 after() 轮询消费，
绝不从子线程直接操作控件。
"""

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from . import config, engine as engine_mod, history

# ---------------------------------------------------------------- PCL2 蓝白主题

BG = "#FFFFFF"          # 主背景
PANEL = "#F5F8FB"       # 侧栏 / 卡片底
PANEL2 = "#E9F1F8"      # 次级面板 / 悬浮底色
BORDER = "#D5E1EC"      # 描边
FG = "#223241"          # 主文字
DIM = "#6F7F8F"         # 次要文字
ACCENT = "#2C8BC8"      # PCL 主题蓝
ACCENT_DARK = "#1F74A8"
ACCENT_LIGHT = "#D8EAF7"  # 浅蓝选中 / 悬停
GREEN = "#2E9E5B"
RED = "#D9534F"
YELLOW = "#B07A1F"
BLUE = "#2C8BC8"
CODE = "#14507E"
LOG_BG = "#F0F5FA"

SEV_COLOR = {100: RED, 90: "#C96A2E", 80: YELLOW, 70: BLUE, 60: BLUE, 50: DIM}

# 字体规范（Windows 使用微软雅黑，其他平台自动回退）
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 11, "bold")
FONT_T = ("Microsoft YaHei UI", 13, "bold")
FONT_LOGO = ("Microsoft YaHei UI", 15, "bold")
FONT_S = ("Microsoft YaHei UI", 9)
FONT_SB = ("Microsoft YaHei UI", 9, "bold")
MONO = ("Consolas", 10)
MONO_S = ("Consolas", 9)


def center(win: tk.Toplevel, w: int, h: int):
    win.update_idletasks()
    x = max(0, (win.winfo_screenwidth() - w) // 2)
    y = max(0, (win.winfo_screenheight() - h) // 3)
    win.geometry(f"{w}x{h}+{x}+{y}")


class ToolTip:
    """轻量悬浮提示。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tw = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _e):
        if self.tw:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tw, text=self.text, bg="#3A4756", fg="#FFFFFF",
                 font=FONT_S, padx=9, pady=5, justify="left").pack()
        self.tw.attributes("-topmost", True)

    def _hide(self, _e):
        if self.tw:
            self.tw.destroy()
            self.tw = None


def make_scrollable(parent, bg):
    """返回 (canvas, inner, scrollbar)：可滚动的帧容器（支持滚轮）。"""
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
    sb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview,
                      bg=PANEL2, activebackground=BORDER, relief="flat", bd=0)
    inner = tk.Frame(canvas, bg=bg)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)

    def _set_width(e):
        canvas.itemconfigure(win, width=e.width)

    def _set_scroll(e):
        canvas.configure(scrollregion=canvas.bbox("all"))

    inner.bind("<Configure>", _set_width)
    inner.bind("<Configure>", _set_scroll, add="+")
    for w in (canvas, inner):
        w.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        w.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        w.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
    return canvas, inner, sb


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
        self.issue_cards = {}       # sig -> (card, body, bar, ...) 卡片控件
        self.plan_vars = []         # [(tk.BooleanVar, item)]
        self.auto_round_sigs = set()
        self.chat_history = []      # [{"role","content"}]
        self.chat_content = ""      # 纯文本累积（测试与调试用）
        self._scan_after_id = None
        self._mc_candidates = []    # 设置页候选 .minecraft 列表
        self._started = time.time()

        self._build_root()
        self._build_body()
        self._build_statusbar()

        self.engine.start_watching()
        self.root.after(400, self._startup)
        self.root.after(150, self._poll)

    # ================================================================ 根窗口

    def _build_root(self):
        self.root = tk.Tk()
        self.root.title("TotemFix")
        self.root.configure(bg=BG)
        self.root.option_add("*Font", FONT)
        self.root.geometry("1120x740")
        self.root.minsize(940, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0,
                        tabmargins=(0, 10, 0, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                        padding=(18, 9), borderwidth=0, font=FONT)
        style.map("TNotebook.Tab",
                  background=[("selected", BG)],
                  foreground=[("selected", ACCENT)])

    def _make_button(self, master, text, command=None, primary=False,
                     big=False, danger=False, tip=None):
        """primary：PCL 蓝底白字；普通：白底蓝字带浅描边；带悬停反馈。"""
        bg = ACCENT if primary else BG
        fg = "#FFFFFF" if primary else (RED if danger else ACCENT)
        hover_bg = ACCENT_DARK if primary else ACCENT_LIGHT
        active_bg = ACCENT_DARK if primary else ACCENT_LIGHT
        b = tk.Button(master, text=text, command=command, bg=bg, fg=fg,
                      activebackground=active_bg, activeforeground=fg,
                      relief="flat", bd=0, cursor="hand2",
                      highlightthickness=1,
                      highlightbackground=ACCENT if primary else BORDER,
                      highlightcolor=ACCENT,
                      padx=18 if big else 12, pady=8 if big else 5,
                      font=FONT_B if big else FONT)
        b.bind("<Enter>", lambda e: b.config(bg=hover_bg))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        if tip:
            ToolTip(b, tip)
        return b

    # ================================================================ 顶栏（内容区工具栏）

    def _build_toolbar(self):
        bar = tk.Frame(self.content, bg=BG)
        bar.pack(side="top", fill="x")
        tk.Frame(bar, bg=BORDER, height=1).pack(side="bottom", fill="x")

        self.btn_scan = self._make_button(bar, "🔍 立即扫描", self._on_scan_click,
                                          primary=True, big=True,
                                          tip="扫描 PCL 与 Minecraft 的全部日志和崩溃报告")
        self.btn_scan.pack(side="left", padx=(16, 6), pady=10)

        self.autofix_var = tk.BooleanVar(value=bool(self.cfg.get("autofix")))
        cb = tk.Checkbutton(bar, text="自动修复", variable=self.autofix_var,
                            command=self._on_autofix_toggle, bg=BG, fg=FG,
                            activebackground=BG, activeforeground=ACCENT,
                            selectcolor=BG, font=FONT,
                            highlightthickness=0, bd=0, cursor="hand2")
        cb.pack(side="left", padx=6)
        ToolTip(cb, "检测到报错后自动分析并执行修复（默认弹窗确认）")

        # 状态点（OpenFrp 风格：● 绿=监控中 / 黄=等待 PCL2）
        self.status_dot = tk.Canvas(bar, width=16, height=16, bg=BG,
                                    highlightthickness=0, bd=0)
        self.status_dot.pack(side="left", padx=(12, 4))
        self._dot_id = self.status_dot.create_oval(3, 3, 13, 13, fill=YELLOW,
                                                   outline="")

        self.watch_label = tk.Label(bar, text="", bg=BG, fg=DIM, font=FONT_S)
        self.watch_label.pack(side="left", padx=4)

        tk.Label(bar, text="DeepSeek 驱动 · 蓝白主题 · 全自动修复",
                 bg=BG, fg=DIM, font=FONT_S).pack(side="right", padx=16)

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

    # ================================================================ 浮动通知

    def _toast(self, msg, kind="info"):
        colors = {"info": ACCENT, "ok": GREEN, "err": RED, "warn": YELLOW}
        if not hasattr(self, "toast_frame") or not self.toast_frame.winfo_exists():
            self.toast_frame = tk.Frame(self.root, bg=BG)
            self.toast_frame.place(relx=0.985, rely=0.955, anchor="se")
        t = tk.Frame(self.toast_frame, bg=BG, highlightthickness=1,
                     highlightbackground=colors.get(kind, ACCENT))
        t.pack(fill="x", pady=3, anchor="e")
        tk.Label(t, text=" " + msg, bg=BG, fg=FG, font=FONT, padx=12, pady=6,
                 justify="left", wraplength=400, anchor="w").pack(side="left")
        self.toast_frame.lift()

        def gone():
            try:
                t.destroy()
            except tk.TclError:
                pass

        self.root.after(4000, gone)

    # ================================================================ 主体

    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        # ================= 左侧导航（OpenFrp 风格侧边栏） =================
        self.nav = tk.Frame(body, bg=PANEL, width=168)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        tk.Frame(self.nav, bg=BORDER, width=1).pack(side="right", fill="y")

        # Logo 区
        logo = tk.Frame(self.nav, bg=PANEL)
        logo.pack(fill="x", padx=14, pady=(16, 14))
        tk.Label(logo, text="🧿 TotemFix", bg=PANEL, fg=ACCENT,
                 font=FONT_LOGO, anchor="w").pack(fill="x")
        tk.Label(logo, text="PCL2 报错检测", bg=PANEL, fg=DIM, font=FONT_S,
                 anchor="w").pack(fill="x")
        tk.Frame(self.nav, bg=BORDER, height=1).pack(fill="x", padx=14)

        # 导航项
        self.nav_items = {}
        self.current_page = 0
        for idx, (text, tip) in enumerate([
                ("🔍 报错检测", "问题列表与 AI 分析、修复计划"),
                ("💬 AI 问答", "向 DeepSeek 自由提问"),
                ("🕘 历史与备份", "操作记录与修复备份还原")]):
            self.nav_items[idx] = self._build_nav_item(text, idx, tip)
        self._refresh_nav()

        # 底部：设置 + 状态
        bottom = tk.Frame(self.nav, bg=PANEL)
        bottom.pack(side="bottom", fill="x", pady=(0, 14))
        self._make_button(bottom, "⚙️ 设置", self._open_settings,
                          tip="路径检测、DeepSeek 密钥与行为设置").pack(
            fill="x", padx=14, pady=(0, 8))
        tk.Label(bottom, text="v2.3 · 本地运行", bg=PANEL, fg=DIM,
                 font=FONT_S).pack()

        # ================= 右侧内容区 =================
        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        self._build_toolbar()

        pages = tk.Frame(self.content, bg=BG)
        pages.pack(fill="both", expand=True)
        self.page_detail = tk.Frame(pages, bg=BG)
        self.page_chat = tk.Frame(pages, bg=BG)
        self.page_history = tk.Frame(pages, bg=BG)
        for p in (self.page_detail, self.page_chat, self.page_history):
            p.grid(row=0, column=0, sticky="nsew")
        pages.grid_rowconfigure(0, weight=1)
        pages.grid_columnconfigure(0, weight=1)

        # 兼容旧属性名（smoke/截图脚本引用）
        self.tab_detail = self.page_detail
        self.tab_chat = self.page_chat
        self.tab_history = self.page_history

        self._build_detail_tab()
        self._build_chat_tab()
        self._build_history_tab()

        self._show_page(0)

    def _build_nav_item(self, text, idx, tip):
        """OpenFrp 风格导航项：左侧 3px 蓝色竖条 + 图标文字，选中白底蓝字。"""
        item = tk.Frame(self.nav, bg=PANEL, height=42, cursor="hand2")
        item.pack(fill="x", pady=2)
        item.pack_propagate(False)
        bar = tk.Frame(item, bg=PANEL, width=3)
        bar.pack(side="left", fill="y")
        lbl = tk.Label(item, text=text, bg=PANEL, fg=FG, font=FONT,
                       anchor="w", padx=12)
        lbl.pack(side="left", fill="both", expand=True)

        def on_click(_e=None, i=idx):
            self._select_tab(i)

        def hover(on, i=idx):
            if i != self.current_page:
                item.config(bg=ACCENT_LIGHT if on else PANEL)
                lbl.config(bg=ACCENT_LIGHT if on else PANEL)

        for w in (item, lbl):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))
        if tip:
            ToolTip(item, tip)
        return (item, bar, lbl)

    def _refresh_nav(self):
        for idx, (item, bar, lbl) in self.nav_items.items():
            sel = idx == self.current_page
            item.config(bg=BG if sel else PANEL)
            bar.config(bg=ACCENT if sel else PANEL)
            lbl.config(bg=BG if sel else PANEL, fg=ACCENT if sel else FG,
                       font=FONT_B if sel else FONT)

    def _show_page(self, idx):
        pages = (self.page_detail, self.page_chat, self.page_history)
        for i, p in enumerate(pages):
            if i == idx:
                p.tkraise()
        self.current_page = idx
        self._refresh_nav()

    def _select_tab(self, idx):
        """兼容旧调用：0=报错检测 1=AI 问答 2=历史与备份。"""
        self._show_page(idx)

    # ------------------------------------------------ 问题卡片列表

    def _render_issue_list(self):
        for w in self.issue_cards_inner.winfo_children():
            w.destroy()
        self.issue_cards = {}
        for it in self.issues:
            sev = SEV_COLOR.get(it["severity"], DIM)
            card = tk.Frame(self.issue_cards_inner, bg=BG, highlightthickness=1,
                            highlightbackground=BORDER, cursor="hand2")
            card.pack(fill="x", padx=8, pady=3)
            bar = tk.Frame(card, bg=sev, width=4)
            bar.pack(side="left", fill="y")
            body = tk.Frame(card, bg=BG)
            body.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            row1 = tk.Frame(body, bg=BG)
            row1.pack(fill="x")
            badge = tk.Label(row1, text=" " + it["kind"] + " ", bg=sev, fg="#FFFFFF",
                             font=FONT_SB, padx=1)
            badge.pack(side="left")
            sev_lbl = tk.Label(row1, text=it["severity_text"], bg=BG, fg=sev,
                               font=FONT_SB)
            sev_lbl.pack(side="left", padx=6)
            if it.get("kb"):
                kb_badge = tk.Label(row1, text=" 📚 已知 ", bg=GREEN, fg="#FFFFFF",
                                    font=FONT_SB)
                kb_badge.pack(side="left")
            title = tk.Label(body, text=it["title"][:90], bg=BG, fg=FG, font=FONT,
                             anchor="w", justify="left", wraplength=270)
            title.pack(fill="x", pady=(4, 0))
            meta = tk.Label(body, text=f"{it['source']} · 第 {it['line']} 行",
                            bg=BG, fg=DIM, font=FONT_S)
            meta.pack(anchor="w")
            sig = it["sig"]
            self.issue_cards[sig] = (card, body, bar, badge, sev_lbl, title, meta, sev)
            for wdg in (card, body, row1, badge, sev_lbl, title, meta):
                wdg.bind("<Enter>",
                         lambda e, c=card, b=body: self._card_hover(c, b, True))
                wdg.bind("<Leave>",
                         lambda e, c=card, b=body: self._card_hover(c, b, False))
                wdg.bind("<Button-1>",
                         lambda e, s=sig: self._select_issue_by_sig(s))
                wdg.bind("<Double-Button-1>",
                         lambda e, s=sig: (self._select_issue_by_sig(s),
                                           self._analyze_selected()))
        n = len(self.issues)
        if n:
            self.issue_count_lbl.config(text=f" {n} ", bg=ACCENT)
            self.issue_hint.config(text=f"共 {n} 个问题 · 单击查看，双击直接 AI 分析")
        else:
            self.issue_count_lbl.config(text="", bg=PANEL)
            self.issue_hint.config(text="🎉 未发现明显报错，后台持续监控中…")
            tk.Label(self.issue_cards_inner, text="🎉 未发现明显报错\n\n后台持续监控中，\n出现新报错会自动弹出分析窗口",
                     bg=PANEL, fg=DIM, font=FONT, justify="center").pack(pady=40)
            self._select_issue(None)
        self._refresh_card_states()

    def _card_hover(self, card, body, on):
        for s, (c, b, *_rest) in self.issue_cards.items():
            if (c is card) and s != self.selected_sig and not self._quit_flag:
                try:
                    c.config(bg=ACCENT_LIGHT if on else BG)
                    b.config(bg=ACCENT_LIGHT if on else BG)
                except tk.TclError:
                    pass

    def _refresh_card_states(self):
        for s, (card, body, bar, *_rest) in self.issue_cards.items():
            selected = (s == self.selected_sig)
            try:
                card.config(bg=ACCENT_LIGHT if selected else BG,
                            highlightbackground=ACCENT if selected else BORDER)
                body.config(bg=ACCENT_LIGHT if selected else BG)
                bar.config(bg=ACCENT if selected else self.issue_cards[s][7])
            except (KeyError, IndexError, tk.TclError):
                pass

    def _select_issue_by_sig(self, sig):
        issue = next((i for i in self.issues if i["sig"] == sig), None)
        self._select_issue(issue)

    # ------------------------------------------------ 报错检测页

    def _build_detail_tab(self):
        # ---- 页内左侧：问题卡片列表（原顶栏下移入页内，OpenFrp 双栏布局）
        left = tk.Frame(self.tab_detail, bg=PANEL, width=340)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(left, bg=BORDER, width=1).pack(side="right", fill="y")

        head = tk.Frame(left, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="检测到的问题", bg=PANEL, fg=FG,
                 font=FONT_B, anchor="w").pack(side="left", padx=14, pady=(12, 6))
        self.issue_count_lbl = tk.Label(head, text="", bg=PANEL, fg="#FFFFFF",
                                        font=FONT_SB)
        self.issue_count_lbl.pack(side="right", padx=14, pady=(12, 6))

        list_canvas, self.issue_cards_inner, list_sb = make_scrollable(left, PANEL)
        list_canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))
        list_sb.pack(side="right", fill="y")

        self.issue_hint = tk.Label(left, bg=PANEL, fg=DIM, font=FONT_S,
                                   text="尚未扫描", wraplength=300, justify="left")
        self.issue_hint.pack(fill="x", padx=12, pady=(6, 10))

        # ---- 页内右侧：详情区
        pad = tk.Frame(self.tab_detail, bg=BG)
        pad.pack(side="left", fill="both", expand=True, padx=16, pady=12)

        self.d_title = tk.Label(pad, text="选择左侧问题查看详情", bg=BG, fg=FG,
                                font=FONT_T, anchor="w", justify="left")
        self.d_title.pack(fill="x")
        self.d_meta = tk.Label(pad, text="", bg=BG, fg=DIM, font=FONT_S,
                               anchor="w", justify="left")
        self.d_meta.pack(fill="x", pady=(2, 8))

        tk.Label(pad, text="日志摘要", bg=BG, fg=DIM, font=FONT_SB,
                 anchor="w").pack(fill="x")
        logframe = tk.Frame(pad, bg=LOG_BG, highlightthickness=1,
                            highlightbackground=BORDER)
        logframe.pack(fill="x")
        self.d_log = tk.Text(logframe, bg=LOG_BG, fg=FG, bd=0,
                             relief="flat", wrap="word", height=7,
                             font=MONO_S, highlightthickness=0)
        log_scroll = tk.Scrollbar(logframe, command=self.d_log.yview, bg=PANEL2,
                                  activebackground=BORDER, relief="flat", bd=0)
        self.d_log.config(yscrollcommand=log_scroll.set)
        self.d_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        btns = tk.Frame(pad, bg=BG)
        btns.pack(fill="x", pady=10)
        self.btn_analyze = self._make_button(btns, "🤖 AI 分析此报错",
                                             self._analyze_selected, primary=True,
                                             tip="把日志摘要发送给 DeepSeek，返回原因/文件/解决办法")
        self.btn_analyze.pack(side="left")
        self._make_button(btns, "📂 打开文件位置", self._open_file,
                          tip="在资源管理器中定位该日志/崩溃报告").pack(side="left", padx=8)
        self._make_button(btns, "📄 预览文件", self._preview_file,
                          tip="直接查看该日志文件内容").pack(side="left")
        self._make_button(btns, "🤖 分析全部问题", self._analyze_all,
                          tip="依次分析列表中的全部报错").pack(side="right")

        tk.Label(pad, text="AI 分析结果", bg=BG, fg=DIM, font=FONT_SB,
                 anchor="w").pack(fill="x", pady=(4, 2))
        aframe = tk.Frame(pad, bg=BG)
        aframe.pack(fill="both", expand=True)
        self.analysis_canvas, self.analysis_inner, self.analysis_sb = \
            make_scrollable(aframe, BG)
        self.analysis_canvas.pack(side="left", fill="both", expand=True)
        self.analysis_sb.pack(side="right", fill="y")

        # 修复计划区（动态重建）
        self.plan_frame = tk.Frame(pad, bg=BG)
        self.plan_frame.pack(fill="x")

        self.d_empty = tk.Label(pad, text="👈 扫描后从左侧选择一个报错，点击“AI 分析此报错”，\n"
                                          "AI 将给出出错原因、出错文件位置与解决办法。",
                                bg=BG, fg=DIM, font=FONT, justify="left")
        self.d_empty.place(relx=0.5, rely=0.55, anchor="center")

    # ---- 分析结果分区卡片

    def _render_kb_sections(self, entries):
        """在分析区顶部渲染离线资料库命中卡片（不清空容器，供调用方组合）。"""
        self.kb_shown = False
        if not entries:
            return
        self.kb_shown = True
        head_card = tk.Frame(self.analysis_inner, bg=ACCENT_LIGHT,
                             highlightthickness=1, highlightbackground=ACCENT)
        head_card.pack(fill="x", pady=(0, 3))
        tk.Label(head_card, text="📚 资料库命中（离线匹配，无需联网）",
                 bg=ACCENT_LIGHT, fg=CODE, font=FONT_B, padx=12,
                 pady=6, anchor="w").pack(fill="x")
        for e in entries:
            card = tk.Frame(self.analysis_inner, bg=BG, highlightthickness=1,
                            highlightbackground=BORDER)
            card.pack(fill="x", pady=3)
            head = tk.Frame(card, bg=BG)
            head.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(head, text=f"🧾 {e.get('title', '')}", bg=BG, fg=ACCENT,
                     font=FONT_B).pack(side="left")
            tk.Label(head, text=f" {e.get('category', '')} ", bg=BLUE,
                     fg="#FFFFFF", font=FONT_SB).pack(side="left", padx=8)
            tk.Label(card, text=e.get("cause", ""), bg=BG, fg=FG, font=FONT,
                     justify="left", anchor="w", wraplength=600).pack(
                fill="x", padx=12, pady=(4, 0))
            for i, s in enumerate(e.get("solution") or [], 1):
                row = tk.Frame(card, bg=BG)
                row.pack(fill="x", padx=12, pady=1)
                tk.Label(row, text=f"{i}.", bg=BG, fg=GREEN, font=FONT_B).pack(
                    side="left", anchor="n")
                tk.Label(row, text=s, bg=BG, fg=FG, font=FONT, justify="left",
                         anchor="w", wraplength=560).pack(side="left", padx=(4, 0))
            src = e.get("source", "")
            if src:
                tk.Label(card, text=f"出处：{src}", bg=BG, fg=DIM, font=FONT_S,
                         anchor="w", justify="left", wraplength=600).pack(
                    fill="x", padx=12, pady=(2, 8))

    def _analysis_section(self, icon, title, content, fg=FG, content_font=None):
        card = tk.Frame(self.analysis_inner, bg=BG, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=3)
        head = tk.Frame(card, bg=BG)
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(head, text=f"{icon} {title}", bg=BG, fg=ACCENT,
                 font=FONT_B).pack(side="left")
        tk.Label(card, text=content, bg=BG, fg=fg, font=content_font or FONT,
                 justify="left", anchor="w", wraplength=600).pack(
            fill="x", padx=12, pady=(4, 10))

    def _render_analysis(self, rec):
        for w in self.analysis_inner.winfo_children():
            w.destroy()
        issue = self._current_issue()
        if issue is not None:
            self._render_kb_sections(issue.get("kb"))
        if rec is None:
            return
        if rec.get("error"):
            self._analysis_section("❌", "分析失败", rec["error"], fg=RED)
            self._clear_plan_frame()
            return
        self._analysis_section("📋", "总结", rec.get("summary") or "（无）")
        self._analysis_section("🔍", "出错原因", rec.get("cause") or "（无）")
        ef = rec.get("error_file") or ""
        self._analysis_file(ef)
        steps = rec.get("solution_steps") or []
        if steps:
            self._analysis_steps(steps)
        conf_map = {"high": ("高", GREEN), "medium": ("中", YELLOW),
                    "low": ("低", RED)}
        conf_txt, conf_color = conf_map.get(rec.get("confidence"), ("中", YELLOW))
        self._analysis_confidence(conf_txt, conf_color)
        self._render_plan(rec)
        self.analysis_canvas.yview_moveto(0)

    def _analysis_file(self, ef):
        card = tk.Frame(self.analysis_inner, bg=BG, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=3)
        head = tk.Frame(card, bg=BG)
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(head, text="📁 出错文件位置", bg=BG, fg=ACCENT,
                 font=FONT_B).pack(side="left")
        if ef:
            row = tk.Frame(card, bg=BG)
            row.pack(fill="x", padx=12, pady=(4, 10))
            tk.Label(row, text=ef, bg=LOG_BG, fg=CODE, font=MONO,
                     padx=8, pady=3).pack(side="left")
            self._make_button(row, "预览", lambda: self._open_rel(ef, preview=True),
                              tip="查看该文件内容").pack(side="left", padx=8)
            self._make_button(row, "打开位置", lambda: self._open_rel(ef),
                              tip="在资源管理器中定位该文件").pack(side="left")
        else:
            tk.Label(card, text="（AI 未能定位到具体文件）", bg=BG, fg=DIM,
                     font=FONT, padx=12, pady=(4, 10), anchor="w").pack(fill="x")

    def _analysis_steps(self, steps):
        card = tk.Frame(self.analysis_inner, bg=BG, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=3)
        head = tk.Frame(card, bg=BG)
        head.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(head, text="✅ 解决办法", bg=BG, fg=ACCENT,
                 font=FONT_B).pack(side="left")
        for i, s in enumerate(steps, 1):
            row = tk.Frame(card, bg=BG)
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=f"{i}.", bg=BG, fg=ACCENT, font=FONT_B).pack(
                side="left", anchor="n")
            tk.Label(row, text=s, bg=BG, fg=FG, font=FONT, justify="left",
                     anchor="w", wraplength=560).pack(side="left", padx=(4, 0))
        tk.Frame(card, bg=BG, height=8).pack()

    def _analysis_confidence(self, text, color):
        card = tk.Frame(self.analysis_inner, bg=BG, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=3)
        row = tk.Frame(card, bg=BG)
        row.pack(fill="x", padx=12, pady=8)
        tk.Label(row, text="🎯 置信度", bg=BG, fg=ACCENT, font=FONT_B).pack(side="left")
        tk.Label(row, text=f" {text} ", bg=color, fg="#FFFFFF",
                 font=FONT_SB).pack(side="left", padx=8)

    def _open_rel(self, rel, preview=False):
        """打开/预览 AI 给出的 .minecraft 相对路径文件。"""
        mc = self.cfg.get("mc_dir", "")
        if not mc:
            messagebox.showinfo("提示", "尚未配置 Minecraft 文件夹。", parent=self.root)
            return
        p = (rel or "").strip().replace("\\", "/")
        if not p or p.startswith("/") or (len(p) > 1 and p[1] == ":") \
                or ".." in p.split("/"):
            messagebox.showinfo("提示", "该路径不在 Minecraft 文件夹内。", parent=self.root)
            return
        full = os.path.abspath(os.path.join(mc, *p.split("/")))
        if not os.path.exists(full):
            messagebox.showinfo("提示", f"文件不存在：{full}", parent=self.root)
            return
        if preview:
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(100 * 1024)
                w = tk.Toplevel(self.root, bg=BG)
                w.title("文件预览 - " + os.path.basename(full))
                w.transient(self.root)
                center(w, 760, 480)
                t = tk.Text(w, bg=LOG_BG, fg=FG, relief="flat", bd=0,
                            wrap="none", font=MONO_S,
                            highlightthickness=1, highlightbackground=BORDER)
                t.insert("1.0", text)
                t.config(state="disabled")
                t.pack(fill="both", expand=True, padx=10, pady=10)
            except OSError as e:
                messagebox.showerror("预览失败", str(e), parent=self.root)
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", full])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(full)])
        except OSError as e:
            messagebox.showerror("打开失败", str(e), parent=self.root)

    # ---- 修复计划

    def _clear_plan_frame(self):
        for w in self.plan_frame.winfo_children():
            w.destroy()
        self.plan_vars = []

    def _render_plan(self, result):
        self._clear_plan_frame()
        plan = result.get("fix_plan") or []
        if not plan:
            tk.Label(self.plan_frame, text="AI 未生成自动修复计划，请按上方步骤手动操作。",
                     bg=BG, fg=DIM, font=FONT_S).pack(anchor="w", pady=(8, 2))
            return
        tk.Label(self.plan_frame, text="🛠 AI 修复计划（执行前自动备份，可在“历史与备份”页还原）",
                 bg=BG, fg=FG, font=FONT_B).pack(anchor="w", pady=(10, 4))
        icons = {"delete": ("🗑 删除", RED), "rename": ("🔄 重命名", YELLOW),
                 "edit": ("✏️ 替换文本", BLUE), "write": ("📝 写入文件", GREEN)}
        for item in plan:
            var = tk.BooleanVar(value=True)
            icon, color = icons.get(item.get("action"), ("修改", BLUE))
            row = tk.Frame(self.plan_frame, bg=PANEL, highlightthickness=1,
                           highlightbackground=BORDER)
            row.pack(fill="x", pady=2)
            cb = tk.Checkbutton(row, variable=var, bg=PANEL, fg=FG,
                                activebackground=PANEL, selectcolor=BG,
                                highlightthickness=0, bd=0)
            cb.pack(side="left", padx=(8, 2), pady=4)
            tk.Label(row, text=icon, bg=PANEL, fg=color,
                     font=FONT_B).pack(side="left", pady=4)
            tk.Label(row, text=item.get("path", ""), bg=PANEL, fg=CODE,
                     font=MONO).pack(side="left", padx=6, pady=4)
            tk.Label(row, text=item.get("reason", ""), bg=PANEL, fg=DIM,
                     font=FONT_S).pack(side="left", padx=6, pady=4)
            self.plan_vars.append((var, item))
        bar = tk.Frame(self.plan_frame, bg=BG)
        bar.pack(fill="x", pady=(10, 4))
        self._make_button(bar, "执行所选修复", self._apply_selected,
                          primary=True).pack(side="left")
        self._make_button(bar, "全选", lambda: self._plan_check_all(True)).pack(side="left", padx=6)
        self._make_button(bar, "全不选", lambda: self._plan_check_all(False)).pack(side="left")

    def _plan_check_all(self, on):
        for var, _ in self.plan_vars:
            var.set(on)

    def _apply_selected(self):
        items = [item for var, item in self.plan_vars if var.get()]
        if not items:
            messagebox.showinfo("提示", "请先勾选要执行的修复项。", parent=self.root)
            return
        if messagebox.askokcancel("确认执行",
                                  f"将执行 {len(items)} 项修复，执行前会自动备份原文件。\n继续？",
                                  parent=self.root):
            self.engine.apply(items, reason="manual")
            self._set_status(f"正在执行 {len(items)} 项修复…")

    # ------------------------------------------------ AI 问答页（气泡）

    def _build_chat_tab(self):
        chat_canvas, self.chat_inner, chat_sb = make_scrollable(self.tab_chat, BG)
        chat_canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(12, 6))
        chat_sb.pack(side="right", fill="y", pady=(12, 6))
        self.chat_canvas = chat_canvas

        bar = tk.Frame(self.tab_chat, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        self.chat_input = tk.Entry(bar, bg=LOG_BG, fg=FG, insertbackground=FG,
                                   relief="flat", bd=0,
                                   highlightthickness=1, highlightbackground=BORDER,
                                   highlightcolor=ACCENT, font=FONT)
        self.chat_input.pack(side="left", fill="x", expand=True, ipady=8)
        self.chat_input.bind("<Return>", lambda e: self._send_chat())
        self._make_button(bar, "发送 ⏎", self._send_chat, primary=True,
                          tip="回车或点击发送").pack(side="left", padx=(8, 0))

        self._chat_append("sys", "你好！我是内嵌的 Minecraft 故障排查助手（DeepSeek 驱动）。\n"
                                 "可以把报错日志粘贴给我，或直接描述问题，例如：“进游戏就闪退怎么办？”")

    def _chat_append(self, role, text):
        self.chat_content += text + "\n"
        align = {"user": "e", "ai": "w"}.get(role, "center")
        bubble_bg = {"user": ACCENT, "ai": PANEL2, "sys": BG}.get(role, PANEL2)
        fg = {"user": "#FFFFFF", "ai": FG, "sys": DIM}.get(role, FG)
        name = {"user": "你", "ai": "AI", "sys": ""}.get(role, "")
        row = tk.Frame(self.chat_inner, bg=BG)
        row.pack(fill="x", padx=14, pady=4)
        wrap = tk.Frame(row, bg=BG)
        if align == "e":
            wrap.pack(anchor="e")
        elif align == "w":
            wrap.pack(anchor="w")
        else:
            wrap.pack(anchor="center")
        if name:
            tk.Label(wrap, text=name, bg=BG, fg=DIM, font=FONT_S).pack(
                anchor=align if align != "center" else "center")
        b = tk.Label(wrap, text=text, bg=bubble_bg, fg=fg, font=FONT,
                     justify="left", wraplength=640, padx=12, pady=8,
                     highlightthickness=1,
                     highlightbackground=BORDER if role != "user" else ACCENT)
        b.pack()
        self.chat_canvas.update_idletasks()
        self.chat_canvas.yview_moveto(1.0)

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
                 font=FONT_B).pack(anchor="w", padx=16, pady=(12, 2))
        hist_frame = tk.Frame(self.tab_history, bg=BG)
        hist_frame.pack(fill="x", padx=16)
        self.hist_list = tk.Listbox(hist_frame, bg=PANEL, fg=FG, bd=0, height=6,
                                    selectbackground=ACCENT_LIGHT,
                                    selectforeground=FG,
                                    activestyle="none",
                                    highlightthickness=1, highlightbackground=BORDER,
                                    font=FONT_S)
        hscroll = tk.Scrollbar(hist_frame, command=self.hist_list.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.hist_list.config(yscrollcommand=hscroll.set)
        self.hist_list.pack(side="left", fill="both", expand=True)
        hscroll.pack(side="right", fill="y")

        tk.Label(self.tab_history, text="修复备份（.minecraft/errordoctor-backups）",
                 bg=BG, fg=DIM, font=FONT_B).pack(anchor="w", padx=16,
                                                  pady=(12, 2))
        bk_frame = tk.Frame(self.tab_history, bg=BG)
        bk_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.backup_list = tk.Listbox(bk_frame, bg=PANEL, fg=FG, bd=0,
                                      selectbackground=ACCENT_LIGHT,
                                      selectforeground=FG,
                                      activestyle="none",
                                      highlightthickness=1, highlightbackground=BORDER,
                                      font=FONT_S)
        bscroll = tk.Scrollbar(bk_frame, command=self.backup_list.yview, bg=PANEL2,
                               activebackground=BORDER, relief="flat", bd=0)
        self.backup_list.config(yscrollcommand=bscroll.set)
        self.backup_list.pack(side="left", fill="both", expand=True)
        bscroll.pack(side="right", fill="y")

        bar = tk.Frame(self.tab_history, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        self._make_button(bar, "还原所选备份", self._restore_backup).pack(side="left")
        self._make_button(bar, "刷新", self._refresh_history).pack(side="left", padx=8)
        self._make_button(bar, "清空历史", self._clear_history, danger=True).pack(side="right")

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
            self._toast(f"已从备份还原：{orig}", "ok")
            self._refresh_history()
        except Exception as e:
            messagebox.showerror("还原失败", str(e), parent=self.root)

    def _clear_history(self):
        if messagebox.askokcancel("清空历史", "确定清空全部操作历史？", parent=self.root):
            history.clear()
            self._refresh_history()

    # ================================================================ 状态栏

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=PANEL)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=BORDER, height=1).pack(side="top", fill="x")
        self.status = tk.Label(bar, text="启动中…", bg=PANEL, fg=DIM,
                               anchor="w", font=FONT_S, padx=12, pady=4)
        self.status.pack(fill="x")

    def _set_status(self, text):
        try:
            self.status.config(text=text)
        except tk.TclError:
            pass

    # ================================================================ 设置弹窗

    def _open_settings(self):
        if getattr(self, "settings_win", None) and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            return
        w = tk.Toplevel(self.root, bg=BG)
        w.title("设置")
        w.transient(self.root)
        center(w, 640, 780)
        self.settings_win = w

        def section(text):
            tk.Frame(w, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(10, 0))
            tk.Label(w, text=text, bg=BG, fg=ACCENT, font=FONT_B).pack(
                anchor="w", padx=18, pady=(6, 2))

        def row_label(text):
            tk.Label(w, text=text, bg=BG, fg=DIM, font=FONT_S).pack(
                anchor="w", padx=18, pady=(6, 2))

        def make_entry(value="", show=None):
            e = tk.Entry(w, bg=LOG_BG, fg=FG, insertbackground=FG, relief="flat",
                         bd=0, highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, font=MONO)
            e.pack(fill="x", padx=18, ipady=6)
            if show:
                e.config(show=show)
            if value:
                e.insert(0, value)
            return e

        sv = self.sv = {}

        # ---- 路径设置
        section("🔍 路径设置")
        row_label("PCL 启动器文件夹（含 PCL.exe / Plain Craft Launcher 2.exe）")
        sv["pcl"] = make_entry(self.cfg.get("pcl_dir", ""))
        pcl_row = tk.Frame(w, bg=BG)
        pcl_row.pack(fill="x", padx=18, pady=(6, 0))
        self._make_button(pcl_row, "🔎 自动检测", lambda: self._autodetect("pcl"),
                          tip="从本机定位 PCL 文件夹").pack(side="left")
        tk.Label(pcl_row, text="检测不到时：打开 PCL2 软件，TotemFix 会自动定位",
                 bg=BG, fg=DIM, font=FONT_S).pack(side="left", padx=8)

        row_label("Minecraft 文件夹（.minecraft）")
        sv["mc"] = make_entry(self.cfg.get("mc_dir", ""))
        mc_btns = tk.Frame(w, bg=BG)
        mc_btns.pack(fill="x", padx=18, pady=(6, 0))
        self._make_button(mc_btns, "🔎 从 PCL 配置检测",
                          self._detect_mc_fast,
                          tip="优先读取 PCL 日志与设置里的游戏文件夹").pack(side="left")
        self.btn_full_scan = self._make_button(mc_btns, "💾 全盘扫描所有 .minecraft",
                                               self._full_scan_mc,
                                               tip="扫描全部磁盘，列出所有 .minecraft 供选择")
        self.btn_full_scan.pack(side="left", padx=8)

        row_label("检测到的 Minecraft 文件夹（双击选择，附完整路径）：")
        self.mc_listbox = tk.Listbox(w, bg=PANEL, fg=FG, bd=0, height=6,
                                     selectbackground=ACCENT_LIGHT,
                                     selectforeground=FG, activestyle="none",
                                     highlightthickness=1, highlightbackground=BORDER,
                                     font=MONO_S)
        self.mc_listbox.pack(fill="x", padx=18)
        self.mc_listbox.bind("<Double-Button-1>", lambda e: self._use_mc_candidate())
        mc_use = tk.Frame(w, bg=BG)
        mc_use.pack(fill="x", padx=18, pady=(6, 0))
        self._make_button(mc_use, "使用所选", self._use_mc_candidate).pack(side="left")
        tk.Label(mc_use, text="全盘扫描约需 10~60 秒",
                 bg=BG, fg=DIM, font=FONT_S).pack(side="left", padx=8)

        # ---- AI 设置
        section("🤖 DeepSeek 设置")
        row_label("API Key（sk-...，仅保存在本机）")
        sv["key"] = make_entry("", show="*")
        if self.cfg.get("api_key"):
            tk.Label(w, text="（已配置，留空表示保持不变）", bg=BG, fg=DIM,
                     font=FONT_S).pack(anchor="w", padx=18)
        row_label("API 地址（OpenAI 兼容，默认官方）")
        sv["base"] = make_entry(self.cfg.get("api_base", ""))
        row_label("模型")
        sv["model"] = make_entry(self.cfg.get("model", ""))
        tk.Label(w, text="deepseek-chat（推荐）或 deepseek-reasoner（推理更强）",
                 bg=BG, fg=DIM, font=FONT_S).pack(anchor="w", padx=18)

        # ---- 行为设置
        section("⚙️ 行为设置")
        sv["ask"] = tk.BooleanVar(value=bool(self.cfg.get("ask_before_fix", True)))
        sv["auto_analyze"] = tk.BooleanVar(value=bool(self.cfg.get("auto_analyze", True)))
        sv["hide"] = tk.BooleanVar(value=bool(self.cfg.get("hide_on_close", True)))
        sv["interval"] = tk.StringVar(value=str(self.cfg.get("watch_interval", 3)))
        tk.Checkbutton(w, text="修改文件前弹窗确认（弹窗内可勾选“下次不再询问”）",
                       variable=sv["ask"], bg=BG, fg=FG,
                       activebackground=BG, selectcolor=BG,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=18, pady=(4, 0))
        tk.Checkbutton(w, text="发现报错后自动调用 AI 分析",
                       variable=sv["auto_analyze"], bg=BG, fg=FG,
                       activebackground=BG, selectcolor=BG,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=18)
        tk.Checkbutton(w, text="点关闭按钮时最小化后台监控（取消勾选=直接退出）",
                       variable=sv["hide"], bg=BG, fg=FG,
                       activebackground=BG, selectcolor=BG,
                       highlightthickness=0, bd=0).pack(anchor="w", padx=18)
        int_row = tk.Frame(w, bg=BG)
        int_row.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(int_row, text="后台监控间隔（秒）：", bg=BG, fg=DIM,
                 font=FONT_S).pack(side="left")
        tk.Spinbox(int_row, from_=1, to=60, textvariable=sv["interval"], width=5,
                   bg=LOG_BG, fg=FG, bd=0, relief="flat",
                   highlightthickness=1, highlightbackground=BORDER,
                   buttonbackground=PANEL2).pack(side="left")

        bar = tk.Frame(w, bg=BG)
        bar.pack(fill="x", padx=18, pady=16)
        self._make_button(bar, "💾 保存", self._save_settings, primary=True,
                          big=True).pack(side="left")
        self._make_button(bar, "🔌 测试连接", self._test_connection).pack(side="left", padx=8)
        self._make_button(bar, "退出程序", self._quit, danger=True).pack(side="right")

        # 打开时预填快速检测结果
        self._fill_mc_list(config.detect_mc_dirs(self.cfg.get("pcl_dir", "")))

    def _fill_mc_list(self, dirs):
        self._mc_candidates = list(dirs or [])
        current = (self.cfg.get("mc_dir") or "").rstrip("\\/")
        if getattr(self, "mc_listbox", None) and self.mc_listbox.winfo_exists():
            self.mc_listbox.delete(0, "end")
            for d in self._mc_candidates:
                mark = "✓ " if d.rstrip("\\/") == current else "   "
                self.mc_listbox.insert("end", mark + d)

    def _use_mc_candidate(self):
        sel = self.mc_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一个 Minecraft 文件夹。",
                                parent=self.root)
            return
        d = self._mc_candidates[sel[0]]
        self.sv["mc"].delete(0, "end")
        self.sv["mc"].insert(0, d)
        self._set_status(f"已选择 Minecraft 文件夹：{d}")

    def _detect_mc_fast(self):
        dirs = config.detect_mc_dirs(self.sv["pcl"].get().strip())
        self._fill_mc_list(dirs)
        if not dirs:
            messagebox.showinfo("未检测到",
                                "PCL 日志与设置中都没有找到游戏文件夹，可尝试“全盘扫描”。",
                                parent=self.root)
        elif not self.sv["mc"].get().strip():
            self.sv["mc"].insert(0, dirs[0])
            self._set_status(f"已自动填入：{dirs[0]}")

    def _full_scan_mc(self):
        self.btn_full_scan.config(state="disabled", text="⏳ 全盘扫描中…")
        self._set_status("全盘扫描 .minecraft 中（约 10~60 秒）…")

        def worker():
            try:
                dirs = config.scan_all_minecraft_dirs()
            except Exception as e:
                self.q.put({"type": "mc_scan_done", "dirs": [], "error": str(e)})
                return
            self.q.put({"type": "mc_scan_done", "dirs": dirs})

        threading.Thread(target=worker, daemon=True, name="full-scan").start()

    def _autodetect(self, which):
        if which == "pcl":
            p = config.detect_pcl_dir()
            if p:
                self.sv["pcl"].delete(0, "end")
                self.sv["pcl"].insert(0, p)
            else:
                messagebox.showinfo("自动检测",
                                    "未检测到 PCL 文件夹。\n"
                                    "请打开 PCL2 软件——TotemFix 会在后台自动识别运行窗口并定位其位置。",
                                    parent=self.root)
        else:
            self._detect_mc_fast()

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
        self._toast("设置已保存", "ok")
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

        threading.Thread(target=worker, daemon=True).start()

    # ================================================================ 确认弹窗

    def _show_fix_confirm(self, plans):
        """plans: [(issue_title, fix_item)]，返回后按用户选择执行。"""
        if getattr(self, "confirm_win", None) and self.confirm_win.winfo_exists():
            self.confirm_win.destroy()
        w = tk.Toplevel(self.root, bg=BG)
        w.title("确认 AI 修复")
        w.transient(self.root)
        w.attributes("-topmost", True)
        center(w, 640, 480)
        self.confirm_win = w

        tk.Label(w, text="🤖 AI 已分析完成，将执行以下修复：", bg=BG, fg=ACCENT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 6))

        box = tk.Text(w, bg=PANEL, fg=FG, relief="flat", bd=0, wrap="word",
                      height=11, font=FONT,
                      highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="both", expand=True, padx=18)
        self.confirm_text = box
        icons = {"delete": "🗑 删除", "rename": "🔄 重命名", "edit": "✏️ 替换文本",
                 "write": "📝 写入文件"}
        for _title, item in plans:
            box.insert("end", f"· {icons.get(item.get('action'), '修改')} "
                              f"{item.get('path', '')}\n", "code")
            if item.get("reason"):
                box.insert("end", f"   {item['reason']}\n", "dim")
        box.tag_configure("code", foreground=CODE, font=MONO)
        box.tag_configure("dim", foreground=DIM, font=FONT_S)
        box.config(state="disabled")

        tk.Label(w, text="⚠️ 执行前会自动备份原文件到 .minecraft/errordoctor-backups/，"
                         "可随时在“历史与备份”页还原。",
                 bg=BG, fg=YELLOW, font=FONT_S, wraplength=600,
                 justify="left").pack(anchor="w", padx=18, pady=6)

        self.confirm_noask = tk.BooleanVar(value=False)
        tk.Checkbutton(w, text="✔ 下次不再询问，直接自动执行（可在设置中改回）",
                       variable=self.confirm_noask, bg=BG, fg=FG,
                       activebackground=BG, selectcolor=BG,
                       highlightthickness=0, bd=0,
                       font=FONT).pack(anchor="w", padx=18, pady=(4, 8))

        bar = tk.Frame(w, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 14))
        self._make_button(bar, "✅ 执行修复", lambda: self._confirm_result(True),
                          primary=True, big=True).pack(side="left")
        self._make_button(bar, "取消（只看不改）", lambda: self._confirm_result(False),
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
        pcl_ok = config._is_pcl_dir(self.cfg.get("pcl_dir", ""))
        try:
            self.status_dot.itemconfig(self._dot_id,
                                       fill=GREEN if pcl_ok else YELLOW)
        except (tk.TclError, AttributeError):
            pass
        self.watch_label.config(
            text=f"{'监控中' if pcl_ok else '等待 PCL2 启动'}"
                 f" · DeepSeek {'已配置' if key_set else '未配置'}"
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
        elif t == "pcl_detected":
            self._on_pcl_detected(ev)
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
        elif t == "mc_scan_done":
            self._on_mc_scan_done(ev)

    def _trigger_watch_scan(self):
        self._scan_after_id = None
        self.engine.scan("watch")

    def _on_pcl_detected(self, ev):
        pcl_dir = ev.get("dir", "")
        if not pcl_dir or not config._is_pcl_dir(pcl_dir):
            return
        self.cfg["pcl_dir"] = pcl_dir
        if not self.cfg.get("mc_dir"):
            d = config.detect_mc_dir(pcl_dir)
            if d:
                self.cfg["mc_dir"] = d
        config.save(self.cfg)
        self._update_watch_label()
        history.add("config", "检测到 PCL2 已启动，自动定位 PCL 文件夹", pcl_dir)
        self._popup_window(f"✅ 检测到 PCL2 已启动，已自动定位：\n{pcl_dir}")
        self._toast("检测到 PCL2 已启动，已自动定位 PCL 文件夹", "ok")
        self._set_status(f"检测到 PCL2 正在运行，PCL 文件夹：{pcl_dir}")
        self.engine.scan("startup")

    def _on_mc_scan_done(self, ev):
        if getattr(self, "btn_full_scan", None):
            try:
                self.btn_full_scan.config(state="normal", text="💾 全盘扫描所有 .minecraft")
            except tk.TclError:
                pass
        dirs = ev.get("dirs", [])
        if ev.get("error"):
            self._set_status("全盘扫描失败：" + ev["error"])
            return
        self._set_status(f"全盘扫描完成：找到 {len(dirs)} 个 .minecraft 文件夹")
        self._toast(f"全盘扫描完成：找到 {len(dirs)} 个 .minecraft", "ok")
        if getattr(self, "settings_win", None) and self.settings_win.winfo_exists():
            self._fill_mc_list(dirs)
        else:
            self._open_settings()
            self._fill_mc_list(dirs)

    def _on_scan_done(self, ev):
        self.btn_scan.config(state="normal", text="🔍 立即扫描")
        res = ev["result"]
        self.issues = res["issues"]
        self._render_issue_list()
        stats = res["stats"]
        self._set_status(f"扫描完成（{ev['reason']}）：发现 {stats['issue_count']} 个问题 · "
                         f"{stats['scanned_at']}")
        if stats["issue_count"]:
            self._toast(f"扫描完成：发现 {stats['issue_count']} 个问题", "warn")
        if ev["reason"] in ("startup", "manual", "watch"):
            history.add("scan",
                        f"扫描完成，发现 {stats['issue_count']} 个问题"
                        + ("" if not res["errors"] else "（存在路径未配置提示）"))
        if res["errors"] and ev["reason"] in ("startup", "manual"):
            self.issue_hint.config(text="提示：" + "；".join(res["errors"]))

    def _select_issue(self, issue):
        self.selected_sig = issue["sig"] if issue else None
        if issue is None:
            self.d_empty.lift()
            self.d_title.config(text="选择左侧问题查看详情")
            self.d_meta.config(text="")
            self.d_log.config(state="normal")
            self.d_log.delete("1.0", "end")
            self.d_log.config(state="disabled")
            self._render_analysis(None)
            self._clear_plan_frame()
        else:
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
                self._render_analysis(None)
                self._analysis_section("💡", "提示",
                                       "尚未分析。点击上方“🤖 AI 分析此报错”。",
                                       fg=DIM)
                self._clear_plan_frame()
        self._refresh_card_states()

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
        self._render_analysis(None)
        self._analysis_section("⏳", "AI 分析中", "正在请求 DeepSeek，约 10~60 秒…",
                               fg=DIM)
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

    def _on_analyze_done(self, ev):
        self._set_status(f"AI 分析完成：成功 {ev['ok_count']} / 失败 {ev['fail_count']}")
        self._toast(f"AI 分析完成：成功 {ev['ok_count']} / 失败 {ev['fail_count']}",
                    "ok" if ev["ok_count"] else "err")
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
        self._toast(f"修复完成 {ok}/{res['total']} 项（已自动备份，可还原）",
                    "ok" if ok == res["total"] else "warn")
        messagebox.showinfo("修复完成",
                            f"已执行 {ok}/{res['total']} 项修复：\n\n{detail}\n\n"
                            "所有修改前均已自动备份，可在“历史与备份”页还原。",
                            parent=self.root)
        self.engine.scan("after_fix")

    def _on_new_issues(self, issues):
        self._render_issue_list()
        n = len(issues)
        self._popup_window(f"⚠️ 检测到 {n} 个新报错！")
        self._toast(f"检测到 {n} 个新报错，开始自动处理…", "warn")
        self._set_status(f"发现 {n} 个新报错，开始自动处理…")
        history.add("scan", f"检测到 {n} 个新报错",
                    "；".join(i["title"][:60] for i in issues))
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
            self._toast("DeepSeek 连接正常：" + (ev.get("reply") or "")[:60], "ok")
            messagebox.showinfo("连接成功", "DeepSeek 连接正常！\n回复：" + ev.get("reply", ""),
                                parent=self.root)
        else:
            self._toast("DeepSeek 连接失败", "err")
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
            w = tk.Toplevel(self.root, bg=BG)
            w.title("文件预览 - " + os.path.basename(p))
            w.transient(self.root)
            center(w, 760, 480)
            t = tk.Text(w, bg=LOG_BG, fg=FG, relief="flat", bd=0,
                        wrap="none", font=MONO_S,
                        highlightthickness=1, highlightbackground=BORDER)
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
