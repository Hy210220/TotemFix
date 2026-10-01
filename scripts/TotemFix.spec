# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：在项目根目录执行
#   python -m PyInstaller --clean --noconfirm scripts\TotemFix.spec
# 产物：dist\TotemFix.exe —— 一个单文件 exe，直接放进 PCL 文件夹双击即用。
#
# 注意：PyInstaller 以 spec 所在目录为基准解析相对路径，
# 因此这里用 SPECPATH 定位项目根目录（本地与 CI 均适用）。

import os

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))

block_cipher = None

a = Analysis(
    [os.path.join(project_root, 'run.py')],
    pathex=[project_root],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'lib2to3', 'pydoc_data'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TotemFix',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 双击运行，无黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
