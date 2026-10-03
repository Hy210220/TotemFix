# -*- coding: utf-8 -*-
"""GUI 冒烟测试（需 Xvfb）：验证 v2 桌面版完整自动流程。"""

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import config, engine as engine_mod, gui, history  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ed-gui-")
PCL = os.path.join(TMP, "PCL")
MC = os.path.join(TMP, ".minecraft")
os.makedirs(os.path.join(MC, "logs"))
os.makedirs(PCL)
with open(os.path.join(PCL, "Log1.txt"), "w", encoding="utf-8") as f:
    f.write("[10:00:00] 启动游戏\n")
with open(os.path.join(MC, "logs", "latest.log"), "w", encoding="utf-8") as f:
    f.write("java.lang.NullPointerException: test\n"
            "\tat net.minecraft.server.Main.main(Main.java:1)\n")
with open(os.path.join(MC, "config.txt"), "w", encoding="utf-8") as f:
    f.write("old\n")

# 隔离数据目录
config.DATA_DIR = os.path.join(TMP, "data")
config.CONFIG_PATH = os.path.join(config.DATA_DIR, "config.json")
config.HISTORY_PATH = os.path.join(config.DATA_DIR, "history.json")

# 屏蔽阻塞弹窗
_msgs = []
gui.messagebox.showinfo = lambda *a, **k: _msgs.append(("info", a)) or None
gui.messagebox.showerror = lambda *a, **k: _msgs.append(("error", a)) or None
gui.messagebox.askokcancel = lambda *a, **k: True


class FakeClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def analyze(self, excerpt, meta):
        return {"summary": "模拟总结", "cause": "模拟原因",
                "error_file": "config.txt",
                "solution_steps": ["步骤1", "步骤2"],
                "fix_plan": [{"action": "edit", "path": "config.txt",
                              "old_text": "old", "new_text": "ai-fixed",
                              "reason": "模拟修复"}],
                "confidence": "high"}

    def chat(self, history, question):
        return "模拟回复：" + question


engine_mod._default_client_factory = lambda cfg: FakeClient(cfg)

cfg = config.DEFAULTS.copy()
cfg.update({"pcl_dir": PCL, "mc_dir": MC, "api_key": "sk-fake",
            "api_base": "http://fake", "model": "deepseek-chat",
            "autofix": True, "auto_analyze": True,
            "ask_before_fix": True, "hide_on_close": False,
            "watch_interval": 1})


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def wait_cond(cond, app, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        failures.append(name)


print("== 场景 1：启动自动扫描 → 自动分析 → 确认弹窗（勾选下次不再询问）→ 执行修复 ==")
app = gui.App(cfg)
pump(app, 2.5)

check("启动扫描发现问题", len(app.issues) >= 1, f"({len(app.issues)} 个)")
check("问题卡片已渲染", len(app.issue_cards) >= 1)
check("自动分析已启动/完成",
      wait_cond(lambda: app.analyses or getattr(app, "confirm_win", None), app, 6))
check("确认弹窗已弹出", getattr(app, "confirm_win", None) is not None)
if getattr(app, "confirm_win", None):
    check("弹窗列出修复项",
          "config.txt" in app.confirm_text.get("1.0", "end"))
    app.confirm_noask.set(True)          # 勾选“下次不再询问”
    app._confirm_result(True)            # 点击“执行修复”
check("勾选后 ask_before_fix 已持久化", cfg["ask_before_fix"] is False)
check("修复执行完成", wait_cond(
    lambda: os.path.exists(os.path.join(MC, "config.txt"))
    and open(os.path.join(MC, "config.txt")).read() == "ai-fixed\n", app, 6))
check("自动备份已生成", len(app.engine.list_backups()) >= 1)
check("修复完成提示已弹出", wait_cond(
    lambda: any("修复完成" in str(m) for m in _msgs), app, 4))

print("== 场景 2：不再询问模式 → 新报错出现后全自动修复（无确认弹窗）==")
_msgs.clear()
with open(os.path.join(MC, "logs", "latest.log"), "a", encoding="utf-8") as f:
    f.write("\njava.lang.RuntimeException: second error\n"
            "\tat net.minecraft.server.Main.main(Main.java:2)\n")
app.engine.scan("watch")                # 模拟监控发现文件变化
new_seen = wait_cond(lambda: any(
    ("second error" in i["title"]) for i in app.issues), app, 6)
check("新报错被检测", new_seen)
check("全自动执行（无确认弹窗）", wait_cond(
    lambda: getattr(app, "confirm_win", None) is None and any(
        "自动执行" in str(m) or "修复" in str(m) for m in _msgs), app, 6))

print("== 场景 3：内嵌 AI 问答 ==")
app._select_tab(1)
app.chat_input.insert(0, "怎么装光影？")
app._send_chat()
check("AI 问答回复", wait_cond(
    lambda: "模拟回复：怎么装光影？" in app.chat_content, app, 6))

print("== 场景 4：设置弹窗、候选 .minecraft 列表与保存 ==")
app._open_settings()
pump(app, 0.5)
check("设置窗口打开", app.settings_win is not None and app.settings_win.winfo_exists())
if app.settings_win:
    # 候选列表：填充两个 .minecraft 后应显示且可选
    mc2 = os.path.join(TMP, "第二处", ".minecraft")
    os.makedirs(mc2, exist_ok=True)
    app._fill_mc_list([MC, mc2])
    check("候选列表渲染", app.mc_listbox.size() == 2)
    app.mc_listbox.selection_set(1)
    app._use_mc_candidate()
    check("使用所选填入输入框", app.sv["mc"].get() == mc2)
    app.sv["mc"].delete(0, "end")
    app.sv["mc"].insert(0, MC)
    app.sv["key"].insert(0, "sk-newkey123")
    app._save_settings()
check("设置已保存", cfg["api_key"] == "sk-newkey123")

print("== 场景 4.5：检测到 PCL2 启动 → 自动定位并扫描 ==")
cfg["pcl_dir"] = ""          # 模拟尚未定位
app.q.put({"type": "pcl_detected", "dir": PCL})
check("PCL2 进程自动定位", wait_cond(
    lambda: cfg.get("pcl_dir") == PCL, app, 4))
check("自动定位后记录历史", wait_cond(
    lambda: any("检测到 PCL2" in h["summary"] for h in history.list_records(10)),
    app, 4))

print("== 场景 5：历史与备份页 ==")
app._select_tab(2)
app._refresh_history()
check("历史有记录", app.hist_list.size() >= 1)
check("备份列表已渲染", app.backup_list.size() >= 1)

app._quit()
pump(app, 0.5)

print()
if failures:
    print(f"❌ GUI 冒烟测试失败：{failures}")
    sys.exit(1)
print("✅ GUI 冒烟测试全部通过")
