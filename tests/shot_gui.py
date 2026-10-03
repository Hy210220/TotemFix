# -*- coding: utf-8 -*-
"""GUI 截图工具（需 Xvfb + ImageMagick `import`）：
    xvfb-run -a -s "-screen 0 1280x800x24" python3 tests/shot_gui.py
把界面驱动到关键状态并截图到 docs/screenshots/，用于 README 配图。
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errordoctor import config, engine as engine_mod, gui  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(REPO, "docs", "screenshots")

TMP = tempfile.mkdtemp(prefix="totemfix-shot-")
PCL = os.path.join(TMP, "PCL")
MC = os.path.join(TMP, ".minecraft")
os.makedirs(os.path.join(MC, "logs"))
os.makedirs(os.path.join(MC, "mods"))
os.makedirs(PCL)
with open(os.path.join(PCL, "Log1.txt"), "w", encoding="utf-8") as f:
    f.write("[11:22:33] 启动游戏\n[11:22:35] 游戏已退出，退出代码 -1\n")
with open(os.path.join(MC, "logs", "latest.log"), "w", encoding="utf-8") as f:
    f.write("[12:00:01] [main/ERROR]: Failed to load mods\n"
            "java.lang.NullPointerException: Cannot invoke mod loader\n"
            "\tat net.minecraft.server.Main.main(Main.java:42)\n"
            "\tat ModLoader.load(ModLoader.java:100)\n")
with open(os.path.join(MC, "options.txt"), "w", encoding="utf-8") as f:
    f.write("fov:0.5\n")
open(os.path.join(MC, "mods", "badmod.jar"), "wb").write(b"fake")

config.DATA_DIR = os.path.join(TMP, "data")
config.CONFIG_PATH = os.path.join(config.DATA_DIR, "config.json")
config.HISTORY_PATH = os.path.join(config.DATA_DIR, "history.json")
gui.messagebox.showinfo = lambda *a, **k: None
gui.messagebox.showerror = lambda *a, **k: None
gui.messagebox.askokcancel = lambda *a, **k: True


class FakeClient:
    def __init__(self, cfg):
        pass

    def analyze(self, excerpt, meta):
        return {"summary": "某个 Mod 与当前 Minecraft 版本不兼容",
                "cause": "mods/badmod.jar 是为 1.19 版本编译的，无法在 1.20.1 上加载，"
                        "缺失类 badmod.BadEntry 导致初始化崩溃。",
                "error_file": "mods/badmod.jar",
                "solution_steps": ["删除或更换兼容版本的 badmod 模组",
                                   "检查模组下载页面的版本要求",
                                   "重新启动游戏验证问题是否解决"],
                "fix_plan": [
                    {"action": "delete", "path": "mods/badmod.jar",
                     "reason": "模组版本不兼容，建议删除"},
                    {"action": "edit", "path": "options.txt", "old_text": "fov:0.5",
                     "new_text": "fov:1.0", "reason": "重置视野设置避免渲染异常"}],
                "confidence": "high"}

    def chat(self, history, question):
        return ("可以按以下步骤排查：\n1. 检查 mods 文件夹里是否有版本不匹配的模组；\n"
                "2. 查看 crash-reports 中最近一次崩溃报告的 Description 部分；\n"
                "3. 若仍无法解决，把最新日志发到我的问答窗口，我会继续帮你分析。")


engine_mod._default_client_factory = lambda cfg: FakeClient(cfg)

cfg = config.DEFAULTS.copy()
cfg.update({"pcl_dir": PCL, "mc_dir": MC, "api_key": "sk-xxx",
            "api_base": "http://fake", "model": "deepseek-chat",
            "autofix": True, "auto_analyze": True, "ask_before_fix": True,
            "hide_on_close": False, "watch_interval": 1})

app = gui.App(cfg)


def pump(s):
    end = time.time() + s
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def shot(name):
    app.root.update_idletasks()
    app.root.update()
    time.sleep(0.4)
    subprocess.run(["import", "-window", "root",
                    os.path.join(SHOT_DIR, name + ".png")], check=False)


os.makedirs(SHOT_DIR, exist_ok=True)
pump(3.5)   # 启动扫描 + 自动分析完成，确认弹窗已弹出
shot("1-确认弹窗")

if app.confirm_win:
    app.confirm_noask.set(True)
    app._confirm_result(True)
pump(3.0)   # 修复执行 + 重新扫描
if app.issues:
    app._select_issue(app.issues[0])
pump(1.0)
shot("2-主界面详情")

app._select_tab(1)
app.chat_input.insert(0, "进游戏就闪退怎么办？")
app._send_chat()
pump(1.0)
shot("3-AI问答")

app._open_settings()
pump(0.6)
shot("4-设置")

app._quit()
print("screenshots done ->", SHOT_DIR)
