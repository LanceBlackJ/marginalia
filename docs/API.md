# Marginalia 本地后端 API 契约（阶段一）

前端（Electron 渲染进程）与 Python 本地服务（FastAPI）之间的唯一接口约定。
后端以此实现，前端以此调用。任何改动必须同时更新本文件。

## 0. 通用约定

- Base URL：`http://127.0.0.1:{port}`，仅监听回环地址。
- 鉴权：除 `GET /api/health` 外，所有 `/api/*` 请求必须带 `Authorization: Bearer <token>`。
- 请求/响应均为 UTF-8 JSON（PDF 文件与 SSE 除外）。时间一律 ISO 8601 UTC 字符串。
- 错误：HTTP 4xx/5xx，响应体 `{"error": {"code": "string", "message": "中文可展示的说明"}}`。
  常用 code：`not_found` `bad_request` `unauthorized` `conflict` `no_api_key` `not_configured`
  `auth_failed` `rate_limited` `timeout` `network` `provider_error` `context_too_long` `internal`。
- 分页：`limit`（默认 50，最大 200）、`offset`。
- PDF 坐标：`bbox = [x0, y0, x1, y1]`，单位 PDF point（1/72 英寸），**左上角为原点**，
  已按页面旋转归一化；与 `width/height`（point）一起给出，前端用 `viewport.scale` 直接换算成 CSS 像素。
- ID：字符串（十六进制），稳定不变。`ord` 是论文内段落序号（整数，0 起），AI 引用写作 `[P{ord}]`。

## 1. 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 无需鉴权。`{"status":"ok","version":"0.1.0"}` |
| GET | `/api/system/info` | `{version, data_dir, db_path, papers_dir, paper_count, disk_usage_bytes}` |

## 2. 文献库

### 对象

`PaperSummary`
```json
{
  "id": "…", "title": "…", "authors": ["…"], "year": 2023, "venue": "…", "doi": "10.x/y",
  "has_pdf": true,
  "status": "ready | parsing | failed | metadata_only",
  "parse_error": null,
  "page_count": 12, "language": "en",
  "favorite": false, "reading_page": 1, "read_progress": 0.0,
  "last_opened_at": null, "imported_at": "…",
  "source_folder": "导入时所在的原文件夹（相对路径，可空）",
  "analysis_status": "none | pending | running | ready | failed"
}
```
`PaperDetail` = `PaperSummary` + `abstract`, `source_url`, `original_path`, `arxiv_id`,
`metadata_source`（`pdf | crossref | openalex | manual`），`reading_state`（前端自定义 JSON，原样存取）,
`access_info`（`[{"label":"…","url":"…"}]`，无全文时给出可用来源）。

### 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers` | 查询参数：`q`（标题/作者/DOI/期刊/摘要模糊匹配）、`view=all\|recent\|favorite`、`sort=recent\|title\|author\|year\|imported`、`order=asc\|desc`、`limit`、`offset`。返回 `{"items":[PaperSummary],"total":n}` |
| GET | `/api/papers/{id}` | `PaperDetail` |
| PATCH | `/api/papers/{id}` | 可选字段：`favorite` `title` `reading_page` `read_progress` `reading_state`（对象）`touch`（true 则更新 `last_opened_at`）。返回 `PaperDetail` |
| DELETE | `/api/papers/{id}` | 删除 Marginalia 内的副本与全部分析数据；**绝不触碰用户原文件**。`{"ok":true}` |
| GET | `/api/papers/{id}/file` | PDF 字节流（`application/pdf`，支持 Range） |
| GET | `/api/papers/{id}/outline` | `{"source":"detected\|toc\|none","items":[{"id","title","level","page"}]}` |
| GET | `/api/papers/{id}/passages?page=N` | 某页段落：`{"page":N,"width":595.3,"height":841.9,"items":[Passage]}` |

> `Passage.source`：`text`（PDF 文字层）或 `ocr`（扫描页的本地 OCR，**可能有识别错误**，界面应提示“这段是 OCR 识别的，请以原文为准”）。`/api/papers/{id}/text` 的每一项同样带 `source`。
| GET | `/api/papers/{id}/passages/{ord}` | 单个 `Passage` |
| POST | `/api/papers/{id}/locate` | body `{"text":"选中文字","page":3}`；把选中文字映射到段落：`{"matches":[{"ord","page","bbox","score","section_title"}]}`（score 0–1，按分数降序，最多 5 条） |
| GET | `/api/papers/{id}/search?q=&limit=` | 论文内全文检索：`{"items":[{"ord","page","bbox","section_title","snippet","highlights":[[s,e]],"score"}]}`（`highlights` 为 `snippet` 内字符区间） |

`Passage`：`{"ord":12,"page":3,"kind":"paragraph|caption|reference|formula|figure_text|table","section_title":"3.2 Method","text":"…","bbox":[x0,y0,x1,y1]}`

`kind` 说明：`paragraph` 正文段落；`caption` 图/表标题；`reference` 参考文献条目（检索默认排除）；
`formula` 显示公式区域（文字层可能因数学字体编码而乱码，精确内容请渲染截图交给视觉模型）；
`figure_text` 图内文字（坐标轴标签、图例、子图标签，检索默认排除）；`table` 表格区域文字。
前端遇到未知 kind 应按 `paragraph` 处理。

### 论文结构实体与区域截图

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers/{id}/figures?page=&kind=` | 图/表区域：`{"items":[{"id","page","bbox","kind":"figure\|table","label":"Fig. 1","caption_ord","caption","inner_text"}]}` |
| GET | `/api/papers/{id}/equations?page=` | 显示公式区域：`{"items":[{"id","page","bbox","number":"1","text","passage_ord"}]}` |
| GET | `/api/papers/{id}/regions/image?page=&x0=&y0=&x1=&y1=&dpi=` | 区域（PDF point，左上角原点）渲染为 PNG；不给坐标渲染整页；dpi 50–300 |
| GET | `/api/papers/{id}/equations/{eid}/image?dpi=` | 公式区域截图（默认 200 dpi） |
| GET | `/api/papers/{id}/figures/{fid}/image?dpi=` | 图/表区域截图（默认 150 dpi） |

解析器基于文字片段按基线重组视觉行，识别：左右交替的页眉页脚、栏结构、首字下沉、行内粗体小标题（作为章节）、
图内文字、带编号公式（含分式碎片归并）、图注。解析一份 92 页的论文约 1 秒。

### 书签 / 笔记 / 高亮

| 方法 | 路径 | body → 返回 |
|---|---|---|
| GET/POST | `/api/papers/{id}/bookmarks` | POST `{page,label,passage_ord?,quote?}`（带 `passage_ord` = 书签落在正文某一段，重新解析后按 `quote` 找回，找不到 `stale=true`）→ `Bookmark{id,page,label,created_at,passage_ord,quote,stale}`；GET → `{"items":[…]}` |
| DELETE | `/api/papers/{id}/bookmarks/{bid}` | `{"ok":true}` |
| GET/POST | `/api/papers/{id}/notes` | POST `{text, page?, quote?, passage_ord?, card_id?}` → `Note{id,paper_id,page,quote,passage_ord,text,card_id,created_at,updated_at}` |
| PATCH/DELETE | `/api/notes/{nid}` | PATCH `{text}` → Note；DELETE → `{"ok":true}` |
| GET/POST | `/api/papers/{id}/annotations` | POST `{page, kind:"highlight", color:"yellow\|blue\|green\|pink", text, rects:[[x0,y0,x1,y1],…], note?}` → `Annotation{id,page,kind,color,text,rects,note,created_at}` |
| DELETE | `/api/papers/{id}/annotations/{aid}` | `{"ok":true}` |

## 3. 导入

全部返回 `{"job_id":"…"}`，进度通过任务接口（§4）查询。

| 方法 | 路径 | body |
|---|---|---|
| POST | `/api/import/files` | `{"paths":["C:\\a.pdf", …]}` |
| POST | `/api/import/folder` | `{"path":"C:\\papers","recursive":true}` —— 保留原文件夹相对结构到 `source_folder` |
| POST | `/api/import/doi` | `{"dois":["10.1038/…", …], "metadata_only"?: false}`（可带 `https://doi.org/` 前缀，后端归一化）。`metadata_only=true`：只保存题录，不下载开放获取全文（条目状态 `metadata_only`，之后可用 `attach_pdf` 补上）；默认 false，遇到开放获取全文会自动下载 |

> **扫描版 PDF**：导入结果 `items[]` 里会带 `ocr_job_id`、`ocr_pages`，表示文字正在后台用**本地 OCR**识别（任务类型 `ocr_paper`，进度走 `/api/jobs/{id}`，可取消；识别完成前没有文字层，论文可先阅读原文）。识别完成后该论文的段落带 `source:"ocr"`。本机没有安装 OCR 组件时不创建任务，`message` 里如实说明。
| POST | `/api/import/url` | `{"url":"https://arxiv.org/abs/…", "metadata_only"?: false}`（支持 arXiv 链接、doi.org 链接、直链 PDF；其它链接返回失败说明）。`metadata_only=true` 只保存题录；直链 PDF 没有题录，会如实失败而不是偷偷下载 |

导入任务 `result`：
```json
{"items":[{"input":"C:\\a.pdf","status":"imported|duplicate|metadata_only|failed","paper_id":"…|null","message":"…|null"}]}
```
- `duplicate`：文件哈希或 DOI 已存在，`paper_id` 指向已有论文。
- `metadata_only`：DOI 能解析元数据但没有开放获取全文；论文以无 PDF 状态入库，`access_info` 给出来源链接，用户可自行下载后在论文上附加 PDF（`POST /api/papers/{id}/attach_pdf {"path":"…"}` → `{"job_id"}`）。

## 4. 后台任务

`Job`
```json
{
  "id":"…", "type":"import|import_doi|import_url|attach_pdf|paper_analysis",
  "title":"导入 3 个 PDF", "paper_id":null,
  "status":"pending|running|waiting_approval|paused|completed|failed|cancelled",
  "progress":0.0, "message":"当前步骤说明", "error":null, "result":null,
  "created_at":"…","started_at":null,"finished_at":null,
  "can_cancel":true, "can_retry":false
}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/jobs?status=&type=&limit=` | `{"items":[Job]}`，按创建时间倒序 |
| GET | `/api/jobs/{id}` | `Job` + `"log":["时间 内容", …]` |
| POST | `/api/jobs/{id}/cancel` | 仅 pending/running |
| POST | `/api/jobs/{id}/retry` | 仅 failed/cancelled，创建新任务返回 `{"job_id"}` |

后端重启后：`running` 的任务会被标记为 `failed`（`error="应用被中断，可重试"`），`pending` 的会继续执行。

## 5. AI 设置、用量与隐私

`AISettings`
```json
{
  "provider_kind":"openai_compatible|anthropic",
  "base_url":"https://api.openai.com/v1",
  "fast_model":"DeepSeek-V4-Flash-0731-W8A8", "strong_model":"MiniMax-M2.7-bf16", "vision_model":"Qwen3-VL-32B-Instruct",
  "embedding_model":"bge-m3", "semantic_retrieval":true,
  "mode":"auto|fast|strong",
  "timeout_s":60, "max_retries":2, "temperature":0.2, "max_output_tokens":4096,
  "auto_analyze":true,
  "price_per_mtok":{"模型名":{"input":0.0,"output":0.0}},
  "monthly_budget": null,
  "has_api_key":false, "configured":false
}
```
`has_api_key`/`configured` 只读。`configured` = 有 Key 且 fast/strong 模型至少配了一个。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/api/settings/ai` | PUT 忽略只读字段，返回最新 `AISettings` |
| PUT | `/api/settings/ai/secret` | `{"api_key":"…"}`；**仅在内存保存，后端绝不落盘**。由 Electron 主进程在启动后/用户修改时推送 |
| DELETE | `/api/settings/ai/secret` | 清除内存中的 Key |
| GET | `/api/papers/{id}/index` | 语义（向量）索引状态：`{model, indexed, total, complete}`；`total` 只统计 ≥20 字的正文/图注/表格段落 |
| POST | `/api/papers/{id}/index` | 创建 `embed_paper` 后台任务建立索引 → `{job_id}`；已完成或未配置嵌入模型时 `job_id=null`。配置了嵌入模型时，导入后会自动建立 |
| POST | `/api/settings/ai/test` | 连接测试：`{"fast":{"ok":true,"model":"…","latency_ms":820,"error":null},"strong":{…}}`（未配置的模型返回 `null`） |
| GET | `/api/usage?month=YYYY-MM` | `{"month","calls","prompt_tokens","completion_tokens","estimated_cost":null,"by_model":[{"model","calls","prompt_tokens","completion_tokens","estimated_cost"}],"by_task_type":[{"task_type","calls","prompt_tokens","completion_tokens"}],"budget":null,"over_budget":false}`（只有配置了价格才有 `estimated_cost`，否则为 null） |
| GET | `/api/privacy/requests?limit=50&before_id=&task_type=` | 发送给云端模型的内容记录，新的在前，**游标分页**：`limit`（1–200，默认 50，旧调用不变）；`before_id` 只返回 id 更小的（取更早的一页）；`task_type` 按任务类型筛选。返回 `{"items":[{"id","ts","task_type","model","paper_id","paper_title","passage_count","chars_sent","includes":{"question":true,"selection":true,"notes":false,"supplements":false,"code":false},"summary":"发送了 8 段原文（约 5,300 字）"}],"has_more":true,"total":42,"task_types":[{"task_type":"ask","count":30}]}`；`total` 是符合筛选的总条数，`task_types` 是各类型条数（不受筛选影响，供下拉使用）。取下一页：把本页最后一条的 `id` 作为 `before_id`。 |

## 6. AI 调用（SSE）

SSE 帧格式：`event: <name>\ndata: <json>\n\n`。前端用 `fetch` + `ReadableStream` 解析（需带鉴权头，`EventSource` 不可用）。
客户端中断请求（AbortController）即取消生成。

### 6.1 翻译 `POST /api/ai/translate`

body `{"paper_id","text","page":3,"passage_ord":12,"force":false}`

事件：`meta {"model","cached":false}` → `delta {"text":"…"}`\* → `done {"translation","model","cached","usage":{"prompt_tokens","completion_tokens"}}`；失败 `error {"code","message"}`。
命中缓存时直接 `meta{cached:true}` + `done`（无 delta）。翻译不创建研究卡片。

### 6.2 研究问答 `POST /api/ai/ask`

```json
{
  "paper_id":"…",
  "question":"为什么作者提出这个模块？",
  "intent":"free|explain|summarize|term|motivation|formula|figure|experiment",
  "selection":{"text":"选中文字","page":3,"passage_ords":[12]},
  "chain_id":null,
  "save":null
}
```
- `selection` 可空；`passage_ords` 可省略，后端会用 `text/page` 自行定位。
- `intent` 为快捷操作类型（explain/summarize/term 属于即时操作，默认 `save=false`）；`free/motivation/formula/figure/experiment` 属于深入分析，默认 `save=true`。`save` 非空时覆盖默认。
- `question` 对快捷操作可为空字符串（后端按 intent 生成问题）。

事件顺序：

1. `meta {"card_id","chain_id","chain_title","task_type","model","escalated":false,"context":{"passage_ords":[…],"passage_count":8,"chars_sent":5300,"includes":{"question":true,"selection":true,"notes":false,"supplements":false,"code":false}}}`
2. `delta {"text":"…"}`\*  —— 原始 Markdown 增量，包含小节标题（`## 直接回答` 等）和 `[P12]` 引用标记
3. 可选 `escalate {"reason":"…","model":"…"}` —— 快速模型结果不可靠，已自动升级强模型；前端清空已显示内容并接收新的 delta
4. `done {"card": InsightCard}`
5. 失败：`error {"code","message","card_id"}`

`InsightCard`
```json
{
  "id":"…","paper_id":"…","chain_id":"…",
  "object_type":"selection|passage|free",
  "object_label":"被研究对象的简短标签",
  "selection":{"text":"…","page":3,"passage_ords":[12]},
  "question":"…","intent":"free","task_type":"method_analysis","model":"…",
  "status":"complete|failed|cancelled","saved":true,
  "answer":{
    "direct":"markdown，含 [P12] 引用",
    "detail":"markdown 或 null",
    "evidence_notes":[{"ord":12,"note":"该段说明了…"}],
    "inferences":[{"text":"AI 推断内容","basis_ords":[12,30]}],
    "follow_ups":["可继续研究的问题"]
  },
  "raw_markdown":"…",
  "evidence":[{"ord":12,"page":3,"section_title":"3.2 Method","quote":"原文段落摘录（取自数据库，非模型生成）","bbox":[0,0,0,0],"kind":"paper_text"}],
  "verification":{"status":"verified|partial|no_evidence|unverified","invalid_citations":[99],"notes":["…"]},
  "usage":{"prompt_tokens":0,"completion_tokens":0},
  "created_at":"…"
}
```
- `evidence` 只包含**真实存在于该论文**的被引用段落；模型编造的 `[P99]` 会进入 `verification.invalid_citations`，前端应以“无法核实”样式展示该标记。
- `answer.inferences` 是 AI 推断，必须与论文原文依据区分展示（小型文字标签）。

### 6.3 研究卡片与分析链

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers/{id}/chains` | `{"items":[{"id","title","object_type","object_label","card_count","updated_at"}]}`（仅含至少有一张已保存卡片的链） |
| GET | `/api/papers/{id}/cards?chain_id=&saved=` | `{"items":[InsightCard]}`，按创建时间升序 |
| PATCH | `/api/cards/{id}` | `{"saved":true}` 或 `{"chain_id":"…"}` → InsightCard |
| DELETE | `/api/cards/{id}` | `{"ok":true}` |
| PATCH | `/api/chains/{id}` | `{"title":"…"}` |
| DELETE | `/api/chains/{id}` | 同时删除链下卡片 |

### 6.4 论文整体分析

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers/{id}/analysis` | 见下 |
| POST | `/api/papers/{id}/analysis/run` | 创建 `paper_analysis` 任务 → `{"job_id"}`（未配置模型返回 `not_configured`） |

```json
{
  "status":"none|pending|running|ready|failed",
  "job_id":null, "model":null, "updated_at":null, "error":null,
  "content":{
    "background":{"text":"…[P3]","evidence_ords":[3]},
    "motivation":{…}, "challenges":{…}, "method":{…}, "modules":{…},
    "experiment_design":{…}, "results":{…}, "conclusion":{…}, "limitations":{…}
  },
  "evidence":[同 InsightCard.evidence],
  "verification":{同上}
}
```
`content` 在 `status≠ready` 时为 `null`。

## 7. Electron 主进程 ↔ 渲染进程（preload 暴露的 `window.paperdesk`）

```ts
interface PaperDeskBridge {
  getBackendInfo(): Promise<{ baseUrl: string; token: string }>;
  getBackendStatus(): Promise<'starting' | 'ready' | 'failed'>;
  onBackendStatus(cb: (s: 'starting' | 'ready' | 'failed', detail?: string) => void): () => void;
  pickPdfFiles(): Promise<string[]>;      // 原生文件选择对话框，多选
  pickFolder(): Promise<string | null>;
  setApiKey(key: string): Promise<void>;  // 主进程用 safeStorage 加密落盘，并推送给后端内存
  clearApiKey(): Promise<void>;
  hasApiKey(): Promise<boolean>;
  openExternal(url: string): Promise<void>;  // 仅允许 http/https
  getAppInfo(): Promise<{ version: string; dataDir: string; platform: string }>;
  windowControls?: …（可选）
}
```
渲染进程**不得**直接访问任意本地文件；原生对话框返回的路径只用于传给后端导入接口。
当 `window.paperdesk` 不存在（在普通浏览器里调试）时，前端从 URL 参数 `?backend=http://127.0.0.1:PORT&token=XXX`
（并缓存到 localStorage）读取后端信息，文件选择退化为文本输入框。


## 文库：文件夹、多特征筛选、智能视图

完整契约见 [API-library.md](API-library.md)。概要：

- `GET/POST /api/folders`、`PATCH/DELETE /api/folders/{id}`、`POST /api/folders/reorder`：文件夹树（最多 6 层，一篇论文只能在一个文件夹里，标签另算）。
- `POST /api/papers/move`：`{paper_ids, folder_id}` 批量移动（`null` = 回到未分类）。
- `GET /api/papers` 新增筛选参数（`folder`、`tags`+`tag_mode`、`note_q`、`annotation_q`、`text_q`、`card_q`、`year_from/to`、`author`、`venue`、`language`、`read`、`parse`、`has_*`、`imported_from/to`，彼此 AND），`sort=relevance`；每篇论文带 `folder_id`、`folder_path`，有文字类条件时带 `matches`（命中片段）。
- `GET /api/papers/facets`：筛选面板的可选值与计数。
- `GET/POST/DELETE /api/smart-views`：保存下来的筛选条件。

## 重新解析已导入的论文

解析器会改进（图内数据归类、表格还原、扫描页 OCR……），但只对新导入的论文生效。库里的旧论文可以“重新解析”，不丢用户数据。

- **解析版本：** `papers.parser_version`（导入或重新解析时记为当前 `PARSER_VERSION`，旧库里的论文是 1）。列表与详情接口带 `parser_version` 与 `parser_outdated`（有 PDF 且版本低于当前）；文库行据此提示“有新版解析，可重新解析”。
- `GET /api/papers/{id}/reparse`：`{can_reparse, parser_version, current_version, outdated, active_job_id, backup}`；`backup` 不为空表示可以撤销最近一次重新解析：`{created_at, expires_at, from_version, to_version, stats}`（保留 7 天，或到下次重新解析）。
- `POST /api/papers/{id}/reparse`（body 可选 `{force}`）→ `{job_id}`：后台任务 `reparse_paper`，**可取消**；补充材料子论文随主论文一起重新解析（不能单独对补充材料操作，返回 400）；没有 PDF（只有题录）返回 400；已有进行中的重新解析时返回它的 `job_id`。任务结果 `{paper_id, items:[{paper_id, title, old, new, stats}], undo_available}`。
  - 解析（和扫描页 OCR）在事务之外做；**派生数据（段落、章节、页面尺寸、图、公式）与各种缓存在一个事务里整体替换**，失败或取消时旧数据完全不变。新解析只得到很少的段落（< 原来的 40%，且原来 ≥ 20 段）时默认中止并提示，`force=true` 才替换；原来是 OCR 识别的论文而本机没有 OCR 组件时中止。
  - 开始前会取消这篇论文上其它还在跑的后台任务（公式/表格识别、语义索引、翻译、分析……），避免它们继续往旧段落序号上写。
  - 完成后与导入后一样自动处理：已配置嵌入模型就重建语义索引；开了“自动分析”就启动公式识别与表格还原（不重做整体分析：它的证据已重映射）。
- `POST /api/papers/{id}/reparse/undo`：回滚到最近一次重新解析之前——旧的派生数据与缓存按原 id 原样恢复，用户数据恢复成改写前的值，重新解析之后新建的笔记等按“新→旧”映射挪回旧段落。只能撤销一次；没有快照或已过期返回 404；正在重新解析时返回 409。

**用户数据的重新映射**（旧段落序号 → 新序号：同一页内按位置框重叠 + 文本相似度；笔记另先按引用原文在新段落里找）：

| 数据 | 处理 |
|---|---|
| 笔记 `notes.passage_ord` | 映射；对不上的 **不删除**：序号置空、`stale=true`（接口 `Note.stale`），保留引用原文 `quote` 与页码，界面标“位置已失效” |
| 批注、书签 | 按页码，不受影响 |
| AI 卡片证据、整体分析、思维导图节点证据（`ords`/`figure_ids`/`equation_ids`）、研究提示、知识条目/候选、复现评估与步骤、论文↔代码关联、一致性报告 | JSON 里的 `ord`/`passage_ord`/`ords`/`evidence_ords`/`passage_ords` 映射；对不上的 `ord` 置 `null` 并加 `stale:true`、`stale_ord` 记下旧值，列表里对不上的移到 `stale_<键>`，**保留页码与引用文字**；指向别的论文的证据不动 |
| 阅读位置 `reading_state.textAnchor` | 映射；对不上就回到页码位置 |
| 复现目标 `repro_targets.ord` | 同笔记 |
| 公式 LaTeX、图理解、表格识别缓存 | 按页码与位置框重新关联到新的公式/图 id（表格按图注段落序号）；对不上的丢弃并重新识别 |
| 语义索引 | 按新段落重建 |
| 翻译缓存 | 按文本哈希，天然保留 |
| 参考文献解析与匹配缓存 | 清空，按新段落重新生成（匹配结果可重新查询） |

快照存在 `reparse_backup`（随论文删除级联清理；7 天后失效，下次访问时清理）。`GET /api/papers/{id}/processing` 与列表 `processing` 在重新解析期间 `reparsing=true`、`overall=processing`、`label="重新解析中"`。
