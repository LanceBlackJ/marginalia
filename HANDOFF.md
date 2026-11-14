# HANDOFF — 交接给下一个 AI

> 写于 2026-11-11。项目名 Marginalia（仓库目录 / 内部代号 PaperDesk）：Windows 桌面端 AI 论文阅读与研究辅助系统。
> 用户用中文交流，**请用中文回复**。本文里"已验证 / 未验证"是诚实记录，别当成保证；动手前先对照代码确认。

---

## 1. 项目是什么、怎么跑

- 技术栈：Electron + React 19 + TypeScript + Vite（electron-vite）；后端 Python 3.13 FastAPI 本地服务；SQLite + FTS5；PDF 解析用 PyMuPDF（**锁 `<2`，当前 1.28.2**）。
- 需求来源：用户给的《完整项目设计说明书 V2.0》（全文只在早期对话里，**不在仓库**）。仓库里以 `docs/API.md`、`docs/API-*.md`、`docs/ARCHITECTURE.md`、`docs/FRONTEND.md`、`docs/STATUS.md` 为准。说明书硬性要求：不得用假数据/假按钮冒充功能、未实现要标"开发中"、AI 事实性回答要有可追溯证据、前后端接口改动必须同步 `docs/API*.md`。
- 目录：
  - `src/main/` Electron 主进程（后端进程管理、safeStorage、IPC）
  - `src/renderer/src/features/*` 各功能前端（`text/` 文字阅读器、`reader/` 目录/右栏、`glossary/`、`figures/` …）
  - `backend/paperdesk/` 后端：`parsing/`（PDF 解析核心）、`library/`（导入、重新解析、任务编排）、`ai/`、`vision/`（视觉/公式/表格 AI）、`api/`（路由）、`code/`、`compare/`、`discovery/`、`knowledge/`、`mindmap/`、`repro/` 等
  - `backend/tests/`（pytest）、`src/**/*.test.ts(x)`（vitest）
- 常用命令（PowerShell，项目根 `E:\Projects\PaperDesk`）：
  - `npm install`、`npm run backend:setup`（建 `backend\.venv`）
  - `npm run dev`（Electron + 自动拉起后端；数据目录 `.devdata`）
  - `npm run backend:test`（pytest）、`npm test`（vitest）、`npm run typecheck`
  - 打包：`npm run dist`（慢；先 `backend:build`）
- 后端手动启动：`backend\.venv\Scripts\python.exe -m paperdesk --port <p> --data-dir <dir>`，环境变量 `PAPERDESK_TOKEN`。API Key **只存后端内存**，重启后需重新 `PUT /api/settings/ai/secret`。

---

## 2. 与用户协作的硬性约定（务必遵守）

1. **界面要黑白极简**（覆盖说明书的蓝色方案）：黑白灰唯一色板，去盒子化（发丝线、留白、字重分层，不用卡片/阴影/彩色标签）。新页面要用截图自查审美。**同一份信息不要在两处重复展示**（左边已有目录，就别在右边再放本文结构）。右侧面板切换用最右一条竖向图标栏（`features/reader/RightRail.tsx`）。
2. **不要问/提议 git commit**。用户自己提交；只有他主动让你提交才做。绝不替他 push。
3. **构建/大测试后清理缓存和临时产物**（`build/backend-work`、`build/backend-dist`、旧 `release/`、`node_modules/.vite`、scratchpad 里的调试数据与临时 venv/模型）。**不能删**：`.devdata`（用户自己的论文库，只读对待）、`build/icon.*`、`node_modules`、`backend/.venv`。删前先看大小，汇报删了什么。
4. **不要启动用户真实的应用窗口**去验证；用独立调试后端 + 内置浏览器（见 §6）。
5. **密钥只经环境变量使用，绝不写进文件/记忆/命令行**（见 §7）。
6. 汇报要诚实：哪些验证了、哪些没验证、哪些仍有问题，都要写清。用户多次问"问题都解决了吗"，不要夸大。
7. 用户的原话：解析慢点没关系（只在导入时解析一次）、"重点是要效果好"、没有库就自己装、需要测试文件就自己找。

---

## 3. 当前工作区状态

- 分支 `main`，最近提交：`285f794 更新了PDF识别的机制`。
- **有 43 个文件未提交**（用户自己提交）。主要是本轮 PDF 解析 / 版面模型 / 本地公式模型 / 目录对齐的改动：
  - 新增：`backend/paperdesk/parsing/layout_model.py`、`library/layout_boxes.py`、`vision/math_local.py`、`backend/requirements-local-math.txt`、`backend/tests/test_layout_boxes.py`、`test_translate_math.py`、`src/renderer/src/features/text/eqRefs.ts(+test)`、`retranslateMath.test.ts`
  - 修改：`parsing/{pdf_parser,layout,metadata,ocr,inline_math,figures,render}.py`、`library/{importer,reparse,processing,import_jobs,ocr_jobs,repo}.py`、`vision/{inline_math_ai,layout_ai}.py`、`ai/{fulltranslate,citeguard,service}.py`、`api/ai.py`、`app.py`、`tests/*`、前端 `TextReader.tsx`、`outlinePos.ts(+test)`、`textStore.ts`、`figViewerStore.ts`、`Lightbox.tsx` 等
- `PARSER_VERSION = 30`（`backend/paperdesk/parsing/pdf_parser.py`，搜 `PARSER_VERSION =`）。改了解析逻辑要 +1（会触发界面"重新解析"提示）。
- 后端测试最近一次：**892 passed / 3 skipped**（全量，约 6.5 分钟）。前端 vitest 和 typecheck 本轮改动后跑过相关部分，交接前请再全量跑一遍。
- 已知偶发失败：`test_materials.py::test_unknown_size_over_auto_limit_is_deferred_to_approval`（下载类用例，单独跑能过，不是本轮引入）。

---

## 4. PDF 解析架构（本轮工作的核心）

流水线：PyMuPDF 取 spans → lines → blocks → passages，规则为主。

**MinerU 主解析器（2026-11-12 新增，PARSER_VERSION 31）**：导入和重新解析先走 MinerU，失败/不可用/扫描件自动回退下面的规则引擎。
- MinerU 装在**独立 Python 3.12 环境** `.local/mineru`（`scripts/setup-mineru.ps1`，约 560 MB；`.local/` 已 gitignore），以 `mineru-kit api-server --port 8765 --tier standard` 作本地服务；后端 3.13 不动，依赖互不冲突。桌面快捷方式 `start-paperdesk.vbs` 会一并隐藏启动它。服务地址可用环境变量 `PAPERDESK_MINERU_URL` 改。
- 代码：`parsing/mineru_client.py`（Tasks API：POST /v1/tasks → PUT 上传 → 轮询 → 下载 zip，读 `middle_json.json`；`ENABLED` 开关、`set_engine` 测试注入、`trust_env=False`）、`parsing/mineru_adapter.py`（middle_json → `ParsedDoc`；bbox 是 0~1 归一化，乘页面尺寸；标题层级按编号推断，MinerU 的 level 全是 2；行内公式把 LaTeX 直接写进正文并登记 `math`；显示公式 LaTeX 写入 `equation_latex`（model='mineru'），编号取自 `\tag{}`）、`parsing/parse_best.py`（`parse_with_mineru`，文字层 <200 字符视为扫描件返回 None）。调用点：`library/importer.py`、`library/reparse.py`；补充材料 `materials/pdf_child.py` 仍用规则解析。MinerU 成功时不再跑版面模型 `layout_boxes` 与行内公式 AI（passage.source='mineru'）。
- 实测（CPU，20 核）：CoT 14 页——标题与现有一致且没有"Equation only/Prompts/Results"误判小节，参考文献 86 = 86；Corridor 8 页——参考文献 27、显示公式 42 个带 LaTeX 与编号、表格 3（规则解析只认出 2）、行内公式 88。阅读器里公式渲染正常。速度：服务首次载入模型约 3~5 分钟，之后每篇约 1~4 分钟。
- **未验证**：中文论文、超长综述（371 条参考文献）、扫描件走回退的流程、打包版（不内置，仅连接外部服务）、MinerU 服务随应用退出（目前不会自动退出）、设置页里没有 MinerU 状态/开关。MinerU 的块级 bbox 对 `passage_rects` 词级高亮的精度没专门测。
- 坑：用 Bash/PowerShell 工具起的后台进程会被回收，长期服务要用终端面板（`run_in_terminal`）起；`.devdata` 老论文需用户"重新解析"才生效。

**规则引擎**（`parsing/layout.py`、`pdf_parser.py`、`reflow.py`、`metadata.py`）负责文字、标题、参考文献、段落合并、页眉页脚等。它每篇论文总会冒出新问题，只能逐个补；新问题先加到 `tests/test_parse_regressions.py` 的合成回归测试（不依赖真实 PDF）。

**本地版面模型 DocLayout-YOLO**（`parsing/layout_model.py` + `library/layout_boxes.py`）：
- 现成预训练 ONNX（`wybxc/DocLayout-YOLO-DocStructBench-onnx`，75 MB，onnxruntime CPU，约 0.6 秒/页），HF 失败自动退 hf-mirror，下载有大小校验。
- **只补表格 / 图 / 显示公式的框，文字仍由规则从文字层取**。让模型决定整页结构（DeepSeek-OCR、DocLayout-YOLO 整页划分、网关视觉模型逐页划分）逐页对比都不如规则，所以不用。
- 表/图框变成 `Region`（`table=True`），框内行成 `figure_text`；公式框给行打 `role="formula"` 且 `region>=10000`，让 `segment_paragraphs` 切出单个公式块。
- 每篇论文框缓存在 `page_boxes` 表；**导入时先检测再解析**，重新解析用缓存；删除论文会清缓存。`layout_boxes.ENABLED` 是总开关（`conftest` 默认关，测试用 `layout_model.set_engine` 注入假引擎）。
- 效果：VGG 表格 0→12、JMLR 1→10、BERT 1→8，无编号显示公式也能认出。

**每页视觉大模型划分**（`vision/layout_ai.py`）：网关里没有好用的版面模型，效果不如规则，`AUTO_ON_IMPORT = False`，仅保留手动接口 `POST /papers/{id}/layout-ai`。

**扫描件 / OCR**：`parsing/ocr.py`、`library/ocr_jobs.py`（会读缓存的 `page_boxes`）。

**行内公式**（`parsing/inline_math.py` 规则 + `vision/inline_math_ai.py` 识别 + `vision/math_local.py` 本地引擎）：
1. 规则从字体还原 TeX；还原不了但像公式的簇记为候选 `{src, tex:null, page, bbox, line}`。
2. 后台任务 `inline_math_ai`：**先本地 texify**（整行截图四周加 40px 白边；输出里的 `$…$` 用"字母数字骨架相似度 + `plausible()`"对回候选，`match_segment`），本地没认出来且已配视觉模型的再交 Qwen3-VL；结果缓存在 `inline_math_ai` 表（`model='texify'` 且 status≠ok 的，配好视觉模型后会重试）。
3. 评估：规则已还原的 87 个公式里 texify 原样找到 82%，Qwen3-VL 70%。**注意：早先"本地模型不行"的结论是输入用错（紧贴字符小图），已更正**；texify 必须喂带留白整行。
4. 没配 AI Key 也会自动启动（`import_jobs.py`、`processing.start_auto` 的门槛已改为"AI 已配置 或 本地引擎可用"）。
5. 本地引擎是**可选重依赖**：`backend/requirements-local-math.txt`（torch ~1 GB、`texify==0.2.1`、**`transformers==4.46.3`**、`tokenizers<0.21`；4.5x 会报 `dict has no to_dict`）。权重 ~600 MB 首次使用下载到 `数据目录/models/hf`。**打包版（PyInstaller）不带**。`conftest` 里 `math_local.installed` 被打成 False，防止测试加载 PyTorch。
6. 速度：模型加载 1–2 分钟，每行 1.5–2 秒。

其它已做的解析修复（PARSER_VERSION 25–29 区间）：表头提示行不当章节、摘要首行粗体误判、中文 `一、/（一）` 章节与跨页汉字续句、无摘要文档、首页脚注/会议页脚归 front、OCR 页竖排戳记丢弃、两行被小字号片段串起来交错（`_split_stacked`）、重复标题（F1000 评审报告）、`_fix_tall_operators`（CMEX `R/Z`→`∫`）、`_demote_panel_labels` 小写续行合并等。细节见 `pdf_parser.py` 顶部的版本注释和 `tests/test_parse_regressions.py`。

---

## 5. 前端：目录与正文对齐

- 文字阅读器 `src/renderer/src/features/text/TextReader.tsx`：首部信息 / Abstract / 参考文献各有同名 `data-sec` 锚点；`jumpSec` ref 记录刚点过的目录项（用户滚动时清除）；`features/reader/outlinePos.ts` 的 `activeSectionAt`、`activeSectionOnScroll`（滚到底取最后一节，但刚点过且标题还在窗口里的优先）+ 单元测试。
- 按 ord 缓存/映射的前端状态，在重新解析后可能错位：`reader.revision()`（段落序号+长度+开头哈希）随 `/papers/{id}/text` 与 `/translation` 返回，`textStore.refreshTranslation` 版本不一致就先重载段落。不要假设 ord 在会话期间不变。
- 翻译里的行内公式占位符保留：`eqRefs.ts`、`retranslateMath.test.ts`、后端 `fulltranslate.py`。

---

## 6. 调试方法（不要碰用户的 `.devdata`）

1. 起独立后端，数据目录放 scratchpad：`backend\.venv\Scripts\python.exe -m paperdesk --port 18099 --data-dir <scratch>\dbgdata`，`PAPERDESK_TOKEN=dbg`。
2. 导入：`POST /api/import/files`，body `{"paths":["E:/…/x.pdf"]}`（**正斜杠**），等 ~15 秒 `GET /api/papers` 看 `status=ready`。
3. `npm run dev` 起 vite（5173）后，用内置浏览器（`mcp__Claude_Browser`）打开 `http://localhost:5173/?backend=http://127.0.0.1:18099&token=dbg#/paper/<id>`（或 `#/reader/<id>`、`#/library`）。第一次可能显示"无法连接后端"，点重试。
4. 改后端代码要重启该后端；前端热更新。测完 `resize_window preset desktop`，杀掉调试后端。
5. 评估解析质量：用 PDF 文字层当真值，按页算 12/14-gram 召回/精度；**但表格文字移出正文会让召回"下降"，别被指标骗了，要看具体页**。语料见 §8。
6. 内置浏览器坑：窗格隐藏时 `requestAnimationFrame` 停摆（`TextReader.onScroll` 的 `rafId.current` 会卡住，高亮永不更新——是测试假象）；截图只有约 800×520，可 `resize_window 800x520` + `document.documentElement.style.zoom='0.625'`。

---

## 7. 环境与坑（这台 Windows 机器）

- **`.ps1` 必须纯 ASCII**（PowerShell 5.1 按 ANSI 读，中文会乱码）。
- npm / pip 已配国内镜像；Electron 二进制可能没下，需 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ node node_modules/electron/install.js`。
- Git Bash 里 `curl` 发中文是 GBK；含中文的请求用 Python/httpx。Git Bash 会把 `/settings/...` 当路径转换，需 `MSYS_NO_PATHCONV=1`。
- **别用 `python - <<EOF` 内联脚本改含中文文件**（stdin 按 GBK 解码）；用 Write 写 `.py` 再 `PYTHONUTF8=1 python 文件`，小改动用 Edit。**脚本/heredoc 里 `\b` 会变成退格字符 `\x08`**（本轮踩了很多次），改完用 `grep -c $'\x08' 文件` 确认；正则用原始字符串。
- 别用 `Get-CimInstance … -match 'npm run dev'` 杀进程（会匹配到自己）。
- JSON 序列化：别把 numpy 标量写进缓存（`numpy.float32` 不可序列化，而"失败不影响导入"会把错误吞掉，只在真实流程里才暴露）。
- `register_schema` 必须在 DB 初始化前执行：新表的模块要在 `app.py` 顶部 import，不要放函数内延迟导入。
- Docker / WSL2 已安装但**复现沙箱的 Docker 路径从未实测**。
- **测试用 LLM 网关**：校园 OpenAI 兼容网关（地址不写进仓库：默认留空，在设置 → AI 模型里自己填；明文 http）。推荐配置：快速 `DeepSeek-V4-Flash-0731-W8A8`，强 `MiniMax-M2.7-bf16`，视觉 `Qwen3-VL-32B-Instruct`。各模型怪癖见记忆 `paperdesk-llm-gateway`（Qwen3-235B 公式密集时漏词、Qwen3-VL-8B 大批量返回空、网关会 429 要退避等）。
  - **Key 由用户放在环境变量 `PD_KEY`**。自动模式分类器会拦"命令里把 Key 推给后端"；解法是在 scratchpad 写启动包装脚本，进程内读 `PD_KEY` 写入 `app.state.ctx.secrets`，Key 不进任何命令/文件/记忆。
- 当前 `backend\.venv` 里已装 torch 2.15、texify 0.2.1、transformers 4.46.3、tokenizers 0.20.3（可选组件）。

---

## 8. 评估语料（找测试文件就自己找，用完删）

- 库里 6 篇真实论文（只读，PDF 在 `.devdata/papers/<id>/original.pdf`）：BHyGNN+ `a6e0f53b…`、CoT `507e63fc…`、Corridor `c1af6e5b…`、Nature `cc282a30…`、LLM 多智能体综述 `58eab8ef…`、超图综述 `9c9ac173…`。参考文献条数期望 68/86/27/39/371/184（曾全对）。BHyGNN+ 的 PDF 已不在 `.devdata`，用过 arXiv 2602.14919 的公开版。
- 另外下载测过：VGG、BERT、JMLR、PReLU、FCN、ResNet、Attention、PLOS、Nature Comms、Frontiers、中文公文、数学论文（Perelman）、把页渲染成纯图造的扫描件。这些文件都已删除，需要时重新找。
- 每次改 `layout.py` / `pdf_parser.py` 前后，逐篇比较：参考文献条数、图表题数、标题列表、无空格长串（正则 `[a-z]{28,}`）。

---

## 9. 已知未解决 / 待办（按重要性）

**解析**
1. **行内公式片段（已修大半，PARSER_VERSION 30）**：碎片的真正根因是规则把 Times 正文论文里 CMR/CMBX 排的括号、等号、`max(0`、粗体向量当成非数学字体，公式在这些地方被切断。现在 `inline_math.non_cm_body` 判断"正文不是 CM"后，这些片段算数学字体（`line_clusters(..., cm_text=True)`）；整簇认不全时记成候选并带 `parts`（小簇兜底），读取方用 `inline_math.flatten_runs` 展开，识别成功去掉 `parts`。验证：PReLU 候选 15→3 个未解决（含本地 texify 认出的 7 个），旧的已还原公式一条不丢（覆盖检查），后端 892 测试通过。**仍剩**：带堆叠分式的（`E[x²] = 1/2`）、`∂E/∂a` 求和式；只在 PReLU、Corridor、Attention、da5527 等几篇验证，**BERT 等没复测**；默认 LaTeX（CM 正文）的论文这套修法不启用（CMR 与正文同字体，分不出来），那类论文的碎片仍靠 texify/视觉模型。
2. 规则还原的 TeX 有时本身是半截（`x_1` 其实是 `\Delta x_1`，`P y i` 被写成 `\sum_{yi}`）；视觉/本地模型更完整。可考虑用 texify 交叉校验规则结果（需先人工裁决分歧）。
3. 仍没修：PLOS 作者贡献里 Conceptualization 等角色标签当 4 级标题；表格里零散短段落；数学论文里"DefineRM"这类大算符下限粘进正文；SnapShot `Tid1Abl1/2` 粘连；显示公式（整行大小分式）的分子仍会并进相邻公式块文字（界面靠公式图/视觉模型显示）；"Equation only / Prompts / Results"这类粗体行内标签仍作 3 级小节；表格单元格仍是文字块。
4. BERT 的公式数从 5 降到 3，**没查是合并还是丢失**。
5. UniMERNet（MinerU 用的）pip 包钉旧依赖，Py3.13 装不上，没测。
6. 模型会漏下标（`s_f` 写成 `sf`），`plausible()` 抓不到。

**未验证**
- **打包版**（PyInstaller）带新版面模型和 torch 依赖：没验证。本地公式引擎默认不进打包版，若要带需单独解决体积和路径。
- 中文期刊论文、质量差的扫描件与新模型的组合。
- 用户真实资料库 / Electron 窗口里的显示（我只在自己的调试环境验证过）。已导入的论文要用户"重新解析"才生效；后端不热加载，改完需提醒用户**完全退出应用再启动**。
- FCN 目录最后两项在浏览器里的表现（只有单元测试）。
- 行内公式识别结果**只在重新打开论文后才显示**（没做实时刷新）；处理状态面板里没显示这一步。
- 复现沙箱 Docker 路径；AI 回答质量没有用真实论文测试集系统评估（说明书第二十一章）。

**可选方向（用户没明确要求，先问再做）**
- arXiv 源码（LaTeX）直接导入，彻底绕过 PDF 解析。
- 行内公式碎片的规则聚合改进。
- 阶段二～五里 `docs/STATUS.md` 标出的未完成项（该文件按阶段一撰写，后续以代码和 `docs/API-*.md` 为准）。

---

## 10. 用户待办（不是 AI 要做的，但别忘了提醒）

- 删除 `E:\fig2_test.png`（临时测试图）。
- **网关 Key 曾粘贴在聊天里，建议轮换**。
- 测试结束后清掉用户级环境变量：`[Environment]::SetEnvironmentVariable("PD_KEY", $null, "User")`。

---

## 11. 更多细节在哪

- 自动记忆目录：`C:\Users\华为\.claude\projects\E--Projects-PaperDesk\memory\`（`MEMORY.md` 为索引）。若下一个 AI 能读到它，优先看：`paperdesk-project-decisions`、`paperdesk-env-quirks`、`paperdesk-ui-preference`、`paperdesk-llm-gateway`、`paperdesk-web-debug`、`paperdesk-pdf-parse-corpus`、`paperdesk-inline-math-vlm`、`paperdesk-outline-body-alignment`、`paperdesk-stale-reader-after-reparse`。其余（术语表、代码讲解、图的 AI 解析、书签、修改上一次提问、仅元数据论文补全文等）功能细节也在那里。
- 完整上一段对话记录：`C:\Users\华为\.claude\projects\E--Projects-PaperDesk\b2d6486b-e9a9-4f59-81be-ae8bf6859987.jsonl`。
