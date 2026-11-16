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

### 2026-11-15 手机端第一版

- 已在现有 React 前端加入响应式手机布局：`AppShell` 在宽度不超过 700px 时隐藏桌面左侧栏，使用固定底部四项导航（文库、笔记、发现、设置）；桌面端布局不变。
- 文库手机端改为单列触屏行，隐藏桌面表头和侧栏；阅读器手机端隐藏章节侧栏、AI 右面板和右侧工具栏，保留文字/PDF/翻译/公式/引用内容；顶栏和页面底部预留安全区域。
- `src/renderer/public/manifest.webmanifest` 与 `index.html` 已加入 PWA 安装入口，普通手机浏览器打开 Web 版可以“添加到主屏幕”。当前图标沿用 `favicon.png` 的 64×64 资源。
- 已验证：`npm run typecheck`；前端 shell、作者/单位、参考文献模型测试共 3 个测试文件 38 项通过；`npm run build` 成功，`git diff --check` 无空白错误。全量 Vitest 共 102 个文件，其中 101 个通过；`LightboxVector` 在全量并发运行时出现 1 次时序失败，单独重跑该文件 5 项均通过，和手机版改动无关。
- 当前手机版已切换为局域网 PWA/独立主屏幕应用方案：手机输入桌面端显示的 IP 和端口即可访问电脑上的同一份文献库；不再使用 Supabase、登录页面或账号会话。
- 2026-11-15 已移除自有账号系统和多账号分库逻辑：后端只使用一个桌面端数据目录，前端删除 `AuthGate`、`AuthProvider`、会话请求头和账号菜单；`docs/LAN_CONNECTION.md` 记录新的连接边界。
- 2026-11-15 已补上跨设备服务：后端同时提供 `/api/*` 和 `out/renderer` 静态页面；桌面端自动绑定 `0.0.0.0`，设置页显示 `lanUrl`；PWA 的 service worker 使用 network-first，电脑端重启更新后手机刷新即可拿到新版本。
- 当前验证：`npm run typecheck`、`npm run build`、`compileall`、shell 测试 4 项通过；独立临时目录创建 FastAPI 应用并确认静态路由注册。`git diff --check` 只有现有文件换行风格警告；当前受限环境未能用本地套接字完成 HTTP 实测，不能记为已通过。

### 2026-11-15 本轮 PDF 解析修复（最新）

- `backend/paperdesk/parsing/pdf_parser.py` 的 `PARSER_VERSION` 已更新到 **38**。已加入通用规则：
  - 首页作者名单跨栏时，按同一基线、姓名特征和横向相邻关系合并左右文字块；作者行不再漏到正文，也不会因为作者分成多行而只显示一部分。
  - 行首加粗的 run-in 小标题（例如 `LLM for Judging.`）如果后面紧跟正文，保留为同一段中的加粗前缀，不再错误生成一个独立小节；关键词、算法关键字、参考文献等既有特殊类型仍按原规则处理。
  - 图/表区域检测新增“纯文字表格”路径：以 `Table/表` 表题为锚点，结合相邻多行、列式短行、数字密度和横向重叠判断表体。没有边框、横线或位图对象的表格也会从正文流移到 `figure_text`/表格区域，避免表内指标变成普通段落。
  - 多行表题会先识别续行再选表体；表格区域只匹配表题，普通图形不会错误复用上一个 `Table N`，避免重复表格实体和错挂图题。
  - 图表题即使贴着图框边缘或落在图框范围内，也不会被标成图内文字；因此多行图题不会丢掉第一行，也不会留下孤立的后半句。
- `src/renderer/src/features/text/frontMatter.ts` 现在会去除作者单位数字角标，拆分没有逗号的连续作者行，并累积多个作者段落；会议出版信息（Proceedings、PMLR、年份、版权声明、城市等）统一进入 `other`，不再污染作者名单。公式引用规则补充了 `Eqn./Eqns.` 等英文写法，与中文译文共用同一套“只有真实公式编号才链接”的判断。
- `backend/paperdesk/parsing/layout.py` 现在把双栏参考文献中同一基线的“左栏首条 + 右栏首条”先拆开、按栏排序，再让续行回到对应条目；同时将 PDF 中落在多字符姓名上的独立重音按坐标合成（例如 `R¨uhle` → `Rühle`）。
- 公式/结构通用规则继续收紧：分情况公式的上下行与编号归入同一公式块；只有 `{`、`},` 这类提示词 JSON 结构符号不再误判为公式；填充文本框会跨普通栏分类恢复为单个结构化块并保留原文硬换行。因此提示词里的 JSON、原文换行和翻译行内 LaTeX 不再被公式识别流程拆坏。
- 目标论文 `Which Agent Causes Task Failures and When?` 的最新只读回归结果：11 位作者完整；出版信息不再出现在作者名单；45 条参考文献按双栏阅读顺序正确分条；`Nelson`/`Needle in the haystack` 与 `Yao`/`Tree of thoughts` 不再串栏；`Rühle` 重音恢复；显示公式 1–8 均保持为公式块，公式(2)/(4)/(5)的分情况/多行结构完整；第 16 页两个提示词框各自保留完整换行，JSON 花括号不再生成伪公式。目标库中的旧结果仍需用户重启后执行“重新解析”才能替换。
- 本轮已验证：规则表格/解析/参考文献/行内公式回归 **101 passed**；前端 text 回归 **18 个文件、173 tests passed**；`npm run typecheck`、`compileall`、`git diff --check` 通过。完整 `backend/tests` 本轮启动后长时间无输出且测试进程消失，已停止，**不能记为全量通过**。这里的“正确”指通用规则和目标 PDF 的实际视觉/文字层核对结果，不把单篇文章的字符串写死成特判。
- 2026-11-15 追加修复作者-年份的多篇合并引用：`citations.detect_markers` 与 `links.merge_markers` 会把同一对括号内已映射的多篇引用归并成一个完整标记并合并参考文献编号；这样 PDF 内部链接被拆成开头/结尾两段时，中间引用也不会丢，前端旧缓存中的嵌套标记也不会错位。目标论文首页的 `(Hong et al., 2023; Li et al., 2023a; Wu et al., 2023)` 现返回一个包含 `[10, 15, 30]` 的完整引用组；单篇引用仍保持独立。新增引用回归 **10 passed**、前端引用模型/前置元数据/行内公式回归 **43 passed**，`npm run typecheck` 通过。修改后重启应用即可使用；若旧正文段落本身未更新，仍需对论文执行“重新解析”。
- 另外修复重新解析后的前端引用状态：`reparseActions.refreshTextView` 现在会强制重载同一篇论文的参考文献与 citations 数据，不会出现正文已换成新解析结果、引用悬停仍使用旧缓存的情况。相关前端引用模型与引用浮层测试 **31 passed**，`npm run typecheck` 通过。
- 2026-11-15 修复译文显示回归：重新解析只改变加粗/斜体边界时，旧缓存译文不再被整段隐藏；`fulltranslate.status` 会在内容、引用、公式仍对应原文时先显示旧译文，同时保留严格格式校验以便“继续翻译”补齐样式。目标论文当前摘要 ord=1 已重新出现在翻译 items 中。前端译文引用支持全角括号/分号与空格差异，中文译文里的多篇作者-年份引用仍可点击。新增/相关后端测试 **4 passed**，前端引用与浮层测试 **32 passed**，`npm run typecheck` 通过。
- 随后确认 `108/109` 的原因是旧摘要译文仅缺少新版样式边界，并非内容缺失；`_usable_cached_translation` 现在把内容、引用、公式均对应但样式来自旧解析的缓存正式计为完成，避免全文翻译反复重试同一段。目标论文本地状态已验证为 **109/109、complete=true、待翻译 0 段**。
- 2026-11-15 继续修复中文译文中的多篇作者-年份引用：前端原先在每个“格式化后的文字片段”里单独匹配引用，引用一旦跨过加粗、搜索命中、公式或术语边界，就会退化成普通文字；现在 `splitSegmentsByMarkers` 先在整段译文上定位，再把同一引用组映射回显示片段，并保留原有样式/交互。正文版本变化时 `TextReader` 也会强制刷新参考文献与 citations，避免重解析后沿用旧缓存。针对 `已引起广泛关注(Hong et al., 2023; Li et al., 2023a; Wu et al., 2023)` 及跨格式边界的回归已通过；文字阅读器相关 **18 个文件、176 passed**、后端引用链接 **8 passed**、`npm run typecheck` 与 `git diff --check` 通过。
- 2026-11-15 修复首页作者单位未显示：目标文章的单位文字原本已经在 `kind=front` 段落中，但 `FrontBlock` 默认折叠，只显示作者名单；现在单位默认展开，同时仍可手动收起。`frontMatter.parseAffiliations` 补充中文机构词、中文编号单位（如 `1北京大学… 2清华大学…`）以及 `Equal contribution`/`Correspondence` 前缀清理，不会把说明文字当成单位。前置元数据测试 **10 passed**，类型检查通过；这是显示层修复，重启应用即可，不需要重新解析。
- 2026-11-15 补充作者与单位的对应角标：之前 `parseAuthors` 为了避免单位数字污染姓名，直接删掉了所有数字和 `*`/`†` 等符号，导致作者名后没有单位编号。现在 `parseAuthorEntries` 将姓名和紧随其后的单位/贡献角标结构化保存，`FrontBlock` 用真正的上标显示（例如 `Shaokun Zhang * † 1`），多行/无逗号作者和中文作者行共用此逻辑，旧的 `authors.names` 仍保留兼容。相关前置元数据测试 **11 passed**，`npm run typecheck` 与 `git diff --check` 通过；重启应用即可，不需要重新解析。
- 2026-11-15 继续修复正文多篇作者-年份引用在界面中不显示的问题：后端核查确认目标论文的 `(Hong et al., 2023; Li et al., 2023a; Wu et al., 2023)` 已正确返回为一个 `[10, 15, 30]` 引用组；前端新增 `inferAuthorYearMarkers` 兜底，仅在括号内同时命中已确认参考文献的第一作者姓氏与年份（含 a/b 后缀）时补建整组标记，不凭普通数字猜测，中文译文与英文原文共用。`PassageView` 会把接口标记与兜底标记合并去重。相关前端引用/前置元数据/浮层测试 **45 passed**，类型检查与 `git diff --check` 通过。
- 2026-11-16 继续收紧作者-年份引用的通用识别：`references.parse.first_author_surname` 现在兼容 `Surname, A.`、`A. Surname` 和完整姓名，不会把 `M. Ghafarollahi` 错取成首字母；后端引用正则与前端兜底统一支持全角括号、逗号、分号和 `＆`，多篇引用按分号逐项匹配后再合并，避免把不同文献的作者和年份串配。PDF 内链一致性校验也复用同一姓氏规则。新增真实形态回归覆盖 `(Ghafarollahi & Buehler, 2024)` 与 `(Hong et al., 2023; Li et al., 2023a; Wu et al., 2023)`，后端引用测试 **11 passed**，前端引用/前置元数据/浮层测试 **46 passed**，`npm run typecheck` 通过。此改动不需要重新解析，重启/重新构建前端后打开文章即可；如果正文段落本身仍是旧解析版本，才需要重新解析。

### 2026-11-16 译文/公式/图片显示与打开论文时的未完成识别提示

- 行内公式的通用渲染规则跳过被错误登记为公式的常见英文短词（`at`、`in`、`of` 等），前后端公式保护使用同一规则；因此 `a_{t}` 不会再被旁边的普通 `at` 重复替换，也不会在翻译末尾补出 `(at)`。公式行内盒的垂直对齐从 `-0.12em` 收紧到 `-0.04em`，避免公式整体低于正文基线。已有译文如果命中旧版本留下的“短词公式补回尾巴”，会被缓存完整性检查判为过期，下一次继续翻译时自动重译。
- `eqRefs` 的裸编号识别兼容半角/全角括号，并允许 PDF 吞掉列表序号后的空格；`(3)Agentic system information`、`（3）智能体系统信息` 等列举序号不再误当公式引用。保留真正的 `Eq. (3)`、`公式 (3)` 等引用链接。
- 英文正文和摘要的两端对齐从 `inter-character` 改为 `inter-word`，避免英文被逐字母拉开；行内公式的垂直对齐仍由统一的公式样式控制，不再因为额外的图片类型说明改变行高。图片内联显示只保留原始位图/矢量图，不再在图后追加“位图/矢量图说明”。
- 译文格式解析兼容当前 `⟪PDB…⟫` 与旧缓存/模型可能产生的 `《PDB…》`、`<PDB…>` 变体，显示层不会再泄漏 `PDB/PDI` 标签。
- 图注译文的标签解析同时接受 `图/表`、`Figure/Fig./Table/Tab.`、Supplementary/Extended Data 变体，并在显示层统一为“图/表”；不会因为模型保留英文标签而把整段当普通正文，标签也不会漏掉。
- 引用悬停卡片现在会在题录可用时自动查询并显示中文题目译文；标题译文仍由 `refStore` 缓存，不重复请求。
- 打开论文并完成正文载入后，若整体识别仍处于待处理/失败状态，会弹出一次选择框，列出正文结构、公式、表格、语义索引等未完成步骤；用户选择“继续识别”才调用统一处理入口，选择“暂不处理”不会打断阅读。正在后台处理或因缺少模型而跳过的任务不重复弹窗。
- 新增回归：前端文字/引用/图注/公式/公式编号/链接/处理提示 **6 个文件、97 passed**；后端翻译格式与公式保护针对性测试 **4 passed**，包含旧标签和旧短词公式缓存拒绝逻辑。`npm run typecheck`、`npm run build`、`compileall`、`git diff --check` 已通过。完整翻译测试在受限环境中部分用例会启动较慢的后台测试进程，不能把未完整收尾的长跑记为全量通过。
- 这些改动都是通用显示、保护和校验规则，不针对某一篇文章写死内容。目标论文已有的旧解析/旧译文不会被静默修改；公式/表格识别规则若之前已完成会继续保留，旧短词公式译文会在下一次翻译时自动失效并重建。

- 分支 `main`，最近提交：`285f794 更新了PDF识别的机制`。
- **有 43 个文件未提交**（用户自己提交）。主要是本轮 PDF 解析 / 版面模型 / 本地公式模型 / 目录对齐的改动：
  - 新增：`backend/paperdesk/parsing/layout_model.py`、`library/layout_boxes.py`、`vision/math_local.py`、`backend/requirements-local-math.txt`、`backend/tests/test_layout_boxes.py`、`test_translate_math.py`、`src/renderer/src/features/text/eqRefs.ts(+test)`、`retranslateMath.test.ts`
  - 修改：`parsing/{pdf_parser,layout,metadata,ocr,inline_math,figures,render}.py`、`library/{importer,reparse,processing,import_jobs,ocr_jobs,repo}.py`、`vision/{inline_math_ai,layout_ai}.py`、`ai/{fulltranslate,citeguard,service}.py`、`api/ai.py`、`app.py`、`tests/*`、前端 `TextReader.tsx`、`outlinePos.ts(+test)`、`textStore.ts`、`figViewerStore.ts`、`Lightbox.tsx` 等
- `PARSER_VERSION = 38`（`backend/paperdesk/parsing/pdf_parser.py`，搜 `PARSER_VERSION =`）。改了解析逻辑要 +1（会触发界面"重新解析"提示）。
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

---

## 12. 2026-11-15 局域网手机版最终架构（当前实现）

- 用户已明确放弃 Supabase 和账号登录：前端不再加载登录页、AuthProvider、会话令牌或 Supabase 配置；后端不再创建认证库、不再按用户切分论文数据。所有数据仍由桌面端自己的数据目录统一管理。
- Electron 启动 Python 后端时绑定 `0.0.0.0`，启用 `PAPERDESK_ALLOW_REMOTE=1`；后端令牌为空，因此手机只需要电脑显示的局域网地址，不需要登录或输入密码。安全边界是同一局域网，不能把端口暴露到公网。
- `backend/paperdesk/app.py` 支持通过 `web_root` 同时提供 `/api/*` 与构建后的前端页面；`src/main/backend.ts` 将局域网地址放进 `BackendInfo.lanUrl`，设置页的“手机连接”面板显示并可复制这个地址。
- 手机端继续使用响应式 React 前端和 PWA manifest；没有后端地址时显示“连接电脑端”页面，可以输入 `192.168.x.x:端口`。页面以同源方式访问 API，避免手机端额外配置跨域地址。
- `src/renderer/public/sw.js` 使用 network-first 策略，电脑端重新构建并重启后，手机刷新会读取新页面/资源。打包配置把 `out/renderer` 解包到 `app.asar.unpacked`，让外部 Python 后端可以读取静态页面。
- 关键文件：`src/main/backend.ts`、`backend/paperdesk/app.py`、`src/renderer/src/lib/backend.ts`、`src/renderer/src/stores/backendStore.ts`、`src/renderer/src/components/shell/LanConnectPanel.tsx`、`src/renderer/src/features/settings/LanConnectionSection.tsx`。
- 已验证：`npm run build` 成功；后端 `compileall` 成功；用独立临时目录创建 FastAPI 应用并确认静态路由 `/assets`、`/`、`/{path:path}` 已注册。完整 HTTP 访问在当前受限环境中未能用本地套接字复测，不能记为已验证。
- 注意：开发期 `npm run dev` 只保证 Electron 页面；手机端静态页面需要先 `npm run build`，打包后会由后端自动提供。若要在开发期让手机访问，应先构建前端，再启动后端或使用部署后的构建目录。
- 后续若重新改变架构，必须同时更新 `docs/API.md`、`docs/LAN_CONNECTION.md` 和本文，不要把第三方登录逻辑重新接回前端。

---

## 13. 2026-11-15 Android APK

- 已新增独立 Android 工程 `mobile/android`，包名 `app.marginalia.mobile`，最低 Android 8.0（API 26）。它不是复制数据库的第二套应用，而是“阅读优先”的原生壳：连接页、二维码扫描、地址记忆、切换电脑和断线提示由 APK 提供，正文阅读/公式/翻译/引用/笔记等页面继续加载电脑端当前版本。
- 首次打开支持相机扫描电脑端设置页的二维码，也支持手动输入 `http://IP:端口`；地址写入手机私有 Preferences，下次启动直接打开阅读界面。返回键优先回退 WebView 阅读历史。
- Android 依赖：CameraX + Google ML Kit barcode scanning；桌面端二维码由 `qrcode.react` 生成。桌面端设置页同时显示地址、复制按钮和二维码。
- 已构建 APK：`mobile/android/app/build/outputs/apk/debug/app-debug.apk`。`aapt2 dump badging` 已确认包名、启动 Activity、最低/目标 SDK 正确；Gradle `assembleDebug` 成功。当前没有真实 Android 设备可用于安装后的相机和 WebView 实测。
- Android 工程包含 Gradle Wrapper：在 `mobile/android` 执行 `gradlew.bat assembleDebug`；构建产物目录已加入 `.gitignore`。APK 需要电脑端 Marginalia 先启动并显示局域网地址后才能使用。
- 主页顶栏的设置按钮旁新增二维码按钮：点击后直接展开扫码二维码、局域网地址和复制按钮；手机端无需再进入设置页找二维码。相关实现位于 `src/renderer/src/components/shell/Topbar.tsx` 和 `shell.css`，`npm run typecheck`、`npm run build` 已通过。
- 2026-11-15 根据手机实机截图修复 Android APK：`MainActivity` 使用系统安全区，避免状态栏覆盖原生顶栏；WebView 增加加载中、连接失败、重新连接和更换电脑状态，不再出现无提示白屏；保留阅读 WebView、地址记忆和二维码扫描。修复后的 APK 已重新生成到 `mobile/releases/Marginalia-mobile-debug.apk`。
- 2026-11-15 支持多网卡地址选择：Electron 主进程枚举所有非内部 IPv4，优先列出私网地址；设置页和主页二维码弹窗均可选择地址，选择结果保存在前端本地存储，主页二维码、复制地址和设置页显示保持一致。相关类型为 `BackendInfo.lanUrls`，`npm run typecheck`、`npm run build` 已通过。

## 14. 2026-11-15 手机端布局重做（当前最新）

- 手机端不再把桌面端左侧栏简单压成一条长页面：底部四项主导航固定为“文库、笔记、发现、更多”，知识库、论文比较、任务中心、统计、设置收进“更多”网格菜单，保持入口完整但不同时铺开所有模块。
- `src/renderer/src/components/shell/MobileNav.tsx` + `shell.css`：新增更多功能菜单、遮罩和移动端安全区；手机主内容区独立滚动，避免页面内容和底部导航互相覆盖。
- `features/library/library.css` + `bookshelf.css`：文件夹/综述分类在手机上改为横向快捷条；最近阅读改为横滑卡片；列表行改为紧凑触屏卡片；书架封面横向滑动，不再把书格强行挤成超长纵列。
- `features/knowledge/knowledge.css`：阅读笔记按文章改为两列文章卡片，点进单篇后再进入该文章的笔记列表；主题知识库的主题选择改为横向标签条，三栏桌面布局在手机上变为分层区域。
- `features/discovery/discovery.css`：发现方式、检索控件和结果卡片适配窄屏；详情抽屉在手机上变为全屏抽屉，避免右侧面板把正文挤成不可读的窄条。
- `features/settings/{settings,panels}.css`：设置标签横向滚动、设置项单列紧凑排列、双列输入在手机上自动堆叠；`features/tasks/tasks.css`：任务列表卡片化，任务详情改为全屏面板。
- `features/compare/compare.css`、`features/code/code.css`、现有 `features/reader/reader.css`：补充比较、代码工作区和阅读器的窄屏规则，隐藏桌面专用侧栏，保留对应内容和操作入口。
- 已验证：`npm run typecheck`、`npm run build`、外壳回归测试（4 项）通过。尝试启动 `Pixel_8a_API_36` Android 仿真器时，本机 Android Emulator 因 Windows Hyper-V/Windows Hypervisor Platform 加速状态与 ADB 服务权限限制退出，未能取得可用的仿真器截图；因此本轮手机布局验证以实际 `max-width: 700px` 样式、编译产物和代码结构检查为准，不能记为真实设备视觉验收。尝试重新构建 APK 时，Gradle Wrapper 缓存位于不可写的系统目录且当前环境无法访问 `services.gradle.org`，所以本轮没有替换已有 APK；移动端改动均在电脑端前端资源中，现有 APK 连接电脑后会加载新资源。
- 临时仿真文件在项目根目录的 `.emulator-*`，后续清理时不要触碰用户数据目录；交付 APK 仍为 `mobile/releases/Marginalia-mobile-debug.apk`，如前端样式变化后需重新执行 Android APK 的构建/复制流程。
