# -*- mode: python ; coding: utf-8 -*-
# Marginalia 后端的 PyInstaller 配置（onedir）。构建：scripts/build-backend.ps1
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# 业务模块里有按名称/函数内延迟导入的写法，整包收集最稳妥
hiddenimports += collect_submodules("paperdesk")

# uvicorn 的协议/循环实现是运行时按字符串选择的
hiddenimports += collect_submodules("uvicorn")

# 需要带上数据文件或原生库的第三方包
for pkg in (
    "pymupdf", "fitz", "docx", "openpyxl", "pandas", "numpy",
    "tree_sitter", "tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript",
    "tree_sitter_java", "tree_sitter_c", "tree_sitter_cpp", "tree_sitter_go", "tree_sitter_rust", "tree_sitter_bash",
):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # 个别包名在当前环境不存在时跳过（由实测兜底）
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["pd_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 体积：后端用不到这些
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "pip", "setuptools", "wheel"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="paperdesk-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 由主进程以 windowsHide 拉起，不会弹出控制台窗口
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="paperdesk-backend")
