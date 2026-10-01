# -*- coding: utf-8 -*-
"""PyInstaller 打包入口（相对导入在作为脚本直接运行时不可用，故单独此文件）。"""

import sys

from errordoctor.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
