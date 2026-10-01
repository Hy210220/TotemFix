# -*- coding: utf-8 -*-
"""入口：python -m errordoctor 或打包后的单文件 exe。

把 exe 直接放进 PCL 文件夹双击即可：
  - 自动把 PCL 文件夹识别为 exe 所在目录；
  - 自动检测 .minecraft 位置（PCL 日志 → Setup.ini → 系统默认）；
  - 启动即扫描，后台常驻监控，出现新报错自动弹出并 AI 分析、按设置自动修复。

参数：
  --autofix   强制开启自动修复
  --no-ask    本次运行修改前不再询问
  --hidden    启动后最小化到后台
"""

import argparse
import sys

from . import config, gui


def main(argv=None):
    parser = argparse.ArgumentParser(description="TotemFix（单文件桌面版）")
    parser.add_argument("--autofix", action="store_true", help="强制开启自动修复")
    parser.add_argument("--no-ask", action="store_true", help="本次运行修改前不再询问")
    parser.add_argument("--hidden", action="store_true", help="启动后最小化到后台")
    args = parser.parse_args(argv)

    cfg = config.load()
    force = {}
    if args.autofix:
        force["autofix"] = True
    if args.no_ask:
        force["no_ask"] = True

    # GUI 内自行处理路径识别与保存
    app = gui.App(cfg, force)
    if args.hidden:
        app.root.iconify()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
