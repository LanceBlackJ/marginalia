# 安装与打包

## 给使用者：直接安装

产物在 `release/`（由 `npm run dist` 生成）：

| 文件 | 说明 |
|---|---|
| `Marginalia-Setup-0.1.0.exe`（约 192 MB） | 安装包（NSIS）：默认安装到当前用户目录，可改安装位置，创建桌面和开始菜单快捷方式。 |
| `Marginalia-0.1.0-win-x64.zip`（约 239 MB） | 便携版：解压后双击 `Marginalia.exe` 即可，不写注册表。 |

- 双击即可使用，不需要安装 Python、Node 或任何命令行工具；后端已经一起打包，随应用自动启动、退出时自动结束。
- 数据位置：`%APPDATA%\Marginalia\data`（如果你之前用过更名前的版本，仍沿用 `%APPDATA%\PaperDesk\data`，文献库不会丢）（文献库、PDF、设置、日志）。**卸载不会删除它**，避免误删你的文献；需要彻底清理时手动删除这个目录。
- 首次启动不需要任何配置。要使用 AI 功能，在“设置 → AI 模型”里填写 API 地址和 API Key（Key 用 Windows 系统加密保存，不会写进安装包）。
- 安装包没有数字签名，Windows SmartScreen 可能提示“未知发布者”，选择“更多信息 → 仍要运行”。

## 给开发者：怎么打包

前置：已执行过 `npm install` 和 `npm run backend:setup`（建好 `backend/.venv`）。

```bash
npm run dist     # 依次：打包后端 → 构建前端 → 生成安装包和便携版 zip（约 10 分钟）
npm run pack     # 只生成未打包目录 release/win-unpacked，用来快速验证
```

分步：

1. `npm run backend:build`：用 PyInstaller（onedir）把 Python 后端打成 `build/backend-dist/paperdesk-backend/`（约 222 MB）。配置在 `backend/paperdesk.spec`，入口是 `backend/pd_entry.py`；PyMuPDF、tree-sitter 各语言包、pandas/numpy/openpyxl/python-docx、uvicorn 的隐式依赖都在配置里整包收集。
2. `npm run build`：electron-vite 构建主进程、预加载脚本和前端。
3. `electron-builder --win`：按 `electron-builder.yml` 把以上产物和后端目录（放到 `resources/backend/paperdesk-backend`）打成安装包和 zip。使用本机 `node_modules/electron/dist` 作为 Electron 运行时，不重新下载。

网络：国内环境下载 electron-builder 的辅助二进制（NSIS、winCodeSign）需要镜像：

```bash
export ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
```

`.npmrc` 里已配置 npm 与 Electron 镜像。

图标：`npm run icon`（`scripts/make-icon.py`）生成 `build/icon.ico` 和 `build/icon.png`，是原创的黑白图形，不依赖任何第三方素材。

## 安全约定

- 打包版不内置任何密钥。开发期的默认密钥种子文件 `.local/default-api.json` 在 `.gitignore` 里，也不在 `electron-builder.yml` 的 `files` 白名单内，不会进入安装包（已检查 `app.asar` 内没有该文件，也没有形似密钥的字符串）。
- 后端只监听 `127.0.0.1`，访问令牌由主进程随机生成并通过环境变量传给后端；打包版同样如此。
- 单实例锁：再次双击只会唤起已有窗口。退出时主进程结束后端整个进程树。

## 已验证（本机 Windows 11）

- `release/win-unpacked/Marginalia.exe`：窗口正常打开，后端自动启动，`/api/health` 返回 200，数据库建在 `userData/data/database/`；关闭窗口后没有残留的 Marginalia 或后端进程。
- 便携版 zip：解压到临时目录后同样启动、健康检查通过、正常退出无残留。
- 安装包：静默安装到临时目录成功（文件、桌面与开始菜单快捷方式、卸载程序都在）；静默卸载后快捷方式和程序目录都已清除。
- 打包出来的后端单独运行：导入《Finding key players …》2.3 MB 正文用时 1.6 秒，解析出 163 段、3 个公式、27 个图表区域；全文检索（FTS5）能命中；公式截图渲染正常。

## 已知限制

- 没有代码签名证书，安装包未签名。
- 只做了 Windows x64。
- 安装包约 192 MB、解压后约 580 MB：后端自带 Python 运行时、PyMuPDF、pandas、numpy 等；没有做体积裁剪。
- 打包版整条“界面里点击导入 PDF”的流程没有做自动化界面测试（Electron 窗口无法在这里被脚本操控）；导入与解析的后端路径用打包后的后端单独测过。
- 应用内没有自动更新；升级方式是运行新版安装包覆盖安装。
