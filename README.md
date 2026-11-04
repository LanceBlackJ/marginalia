<p align="center"><img src="docs/assets/logo.png" alt="Marginalia" width="220" /></p>

# Marginalia

AI 智能文献阅读与研究辅助系统（Windows 桌面端）。以论文为中心：导入 → 阅读 → AI 理解 → 研究卡片 →（后续阶段）代码理解与复现 → 知识积累。

## 现有功能

- **文献库**：导入 PDF（单个/多个/文件夹/DOI/链接），标签、文件夹、筛选，导入后在后台自动完成解析、整体分析、公式识别、表格还原、语义索引、术语表。
- **阅读**：文字 / 对照 / 译文 / PDF / 并排五种界面；目录、缩略图、书签、论文内搜索、高亮、笔记、全文翻译；图、表、公式可点开让 AI 解析。
- **AI 理解**：选中一段后翻译、解释、总结、术语、设计动机、“这段怎么读”；向论文提问，回答附可点击核对的原文依据，论文里没有的内容会说明并改用自身知识或联网资料作答；每篇论文可开多个独立对话，支持粘贴截图与文件。
- **术语表**：导入时一次生成读这篇之前该先弄懂的概念，论文里定义的附原话出处（后端核对），论文默认读者已懂的单独成组并标明是 AI 补充；正文里第一次出现处有虚线下划线。
- **其它**：概览（9 个维度）、思维导图、自测、补充材料、研究提示、多论文比较、主题知识库、发现与推荐（含按主线的学习路线）、期刊会议、代码理解与复现。

AI 生成的内容都是模型的判断，界面上会标明；引用的原文依据由后端核对，对不上的不会显示成“有出处”。真实论文上的 AI 回答质量没有做系统评估。

各阶段完成情况、未完成项与已知问题见 [docs/STATUS.md](docs/STATUS.md)（该文件按阶段一撰写，后续功能以代码和各 `docs/API-*.md` 为准）。

## 环境要求

| 组件 | 要求 | 本项目验证过的版本 |
|---|---|---|
| Windows | 10 / 11 | 11 |
| Node.js | ≥ 20.19 或 ≥ 22.12 | 24 |
| Python | ≥ 3.11 | 3.13 |
| 模型服务 | OpenAI 兼容接口，或 Anthropic Messages 接口（联网 API） | — |

## 安装

```powershell
npm install                 # 前端依赖（.npmrc 已配置 npmmirror 镜像，含 Electron 下载镜像）
npm run backend:setup       # 创建 backend\.venv 并安装后端依赖（含可编辑安装）
```

如果 Electron 二进制没有被下载（`node_modules\electron\dist` 不存在），执行：

```powershell
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'; node node_modules/electron/install.js
```

## 运行

```powershell
npm run dev                 # 开发模式：自动拉起 Python 后端并打开 Electron 窗口
```

开发模式的数据目录是 `<项目>\.devdata`；可用环境变量 `PAPERDESK_DATA_DIR` 指定其它位置。

首次使用 AI 功能前，到 **设置 → AI 模型** 配置：

1. 提供商类型（OpenAI 兼容 / Anthropic）与 API 地址；
2. 快速模型（翻译、基础解释）与强模型（方法分析、公式、全文逻辑）：默认已填入实测中表现稳定的组合（快速 `DeepSeek-V4-Flash-0731-W8A8`、强 `MiniMax-M2.7-bf16`，见 [docs/STATUS.md](docs/STATUS.md)），换用其它服务商时改成对应的模型名，至少保留一个；
3. API Key（经 Electron `safeStorage` 加密保存，后端只在内存中持有）；
4. 点 **连接测试** 确认可用。

> 没有配置时，阅读、导入、检索、笔记、书签、高亮等功能都可以正常使用；翻译、问答、论文分析会提示去设置。

## 测试

```powershell
npm run backend:test        # 后端（pytest，约 50 个用例：解析、导入、任务、检索、AI 链路…）
npm test                    # 前端单元测试（vitest）
npm run typecheck           # 前端类型检查
```

后端的 AI 测试使用确定性的假 LLM（`backend/tests/fakellm.py`），不需要真实 Key，也不会产生费用。
**这些测试验证的是工程链路（检索、路由、引用核实、流式、取消、重试），不是模型回答质量**——回答质量需要用真实论文测试集评估，见 STATUS.md。

## 手动联调（不使用真实 Key）

```powershell
backend\.venv\Scripts\python.exe backend\tests\fake_llm_server.py --port 18766
```

在设置里把 API 地址设为 `http://127.0.0.1:18766/v1`，快速/强模型填 `fast-m` / `strong-m`，Key 任意。这只是测试工具，回答内容是模板化的。

## 目录结构

```
src/main/        Electron 主进程：窗口、后端进程管理、safeStorage、IPC
src/preload/     受控的 window.paperdesk 桥
src/renderer/    React 界面（文献库、阅读器、Insight Flow、设置、任务中心）
backend/paperdesk/
  api/           FastAPI 路由
  parsing/       PDF 全文解析
  library/       导入流水线与在线题录
  ai/            提供商、路由、检索、提示词、证据核实、卡片、整体分析
  jobs.py        持久化后台任务
backend/tests/   后端测试（含合成 PDF 生成器）
docs/            API 契约、架构、前端说明、状态
scripts/         环境搭建与测试脚本
```

## 文档

- [docs/API.md](docs/API.md) — 前后端接口契约
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构与 AI 流水线
- [docs/FRONTEND.md](docs/FRONTEND.md) — 前端结构与 web 调试模式
- [docs/STATUS.md](docs/STATUS.md) — 阶段一完成情况、未完成项、已知问题
