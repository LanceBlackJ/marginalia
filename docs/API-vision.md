# 公式、框架图与图表理解 API（F5）

后端 `backend/paperdesk/vision/`，路由 `backend/paperdesk/api/vision.py`；前端类型 `src/renderer/src/lib/types.vision.ts`，客户端 `lib/api.vision.ts`。
所有路径前缀 `/api`，鉴权同其它接口（Bearer 令牌）。

## 原则
- 截图由 `parsing.render` 渲染（与解析共用 PyMuPDF 锁），发给**视觉模型**（设置里的 `vision_model`，默认 `Qwen3-VL-32B-Instruct`）。每次发送都进隐私日志（`includes.images=true`）并计入用量；没有配置视觉模型时返回 `400 vision_not_configured`，不会悄悄改用别的模型。
- 视觉模型的输出一律当作“待核对的读图结果”：
  - 公式：转写两次（200 / 300 dpi）交叉核对，不一致 → 告警 + 置信度降为 `low`（模型自报的置信度不可靠），并再发一张图让模型逐符号对照两份、裁决哪份更忠实（采用被判更准的一份；裁决看不懂或判不准就保留第一次；无论结果如何告警都保留，并写明差异）。
  - 读图/解读的 JSON 输出被长度限制截断（`finish_reason=length`）时：不把半截输出再喂回去，改用更大的输出上限和“更精简”的要求重试（文本解读还会改用快速模型，因为带思考的强模型会把预算花在推理上）；重试仍失败才使用“丢弃不完整末尾后”的结果，并在 `warnings` / `verification.notes` 里明确写“输出被截断，可能缺内容”。失败时任务日志保留结束原因、token 数和原始输出前 300 字，错误信息区分“被截断”与“不是合法 JSON”。
  - 框架图：模块名与图的文字层核对（`in_text`），引用必须是真实段落，编造的模块/连线被丢弃。
  - 实验图/表格：数值只有在图的文字层原样出现时才标 `exact`+`verified`，其余一律 `approx`（估计）；解读里出现图和所引原文都找不到的数字 → `ungrounded_numbers` + 核实状态 `partial`。
  - 追问：图里没有的内容必须回答“图中看不出”（`answerable=false`）。
- 没有任何接口会返回“看起来很确定但没有核对过”的数值。

## 公式

### `GET /papers/{id}/equations`（解析结果，见 API.md）
`items[{id, page, bbox, number, text, passage_ord}]`。`text` 是 PDF 文字层，数学字体常为乱码，仅供参考。

### `GET /papers/{id}/equations/{eid}/latex`
已缓存的转写；没有则 `status="none"`（**不调用模型**）。
`{equation_id, latex, confidence: high|medium|low|null, status: none|ok|failed, warnings[], error, model, cached}`

### `POST /papers/{id}/equations/{eid}/latex`  body `{force?: bool}`
转写并缓存。成功时 `status="ok"`；模型只返回说明文字/空内容时 `status="failed"`、`latex=null`（失败不当作缓存命中）。
`warnings` 可能包含：花括号/`\left\right`/`\begin\end` 不配对、含 `?` 占位符、“两次独立转写的结果不一致”。

### `POST /papers/{id}/equations/locate`  body `{page?, text?, bbox?}`
把阅读器选区映射到最近的公式（只做位置重叠/文字相似，不调用模型）。`{equation: {id,page,bbox,number,passage_ord,score}|null, candidates[]}`。
前端仅在 `score ≥ 0.5` 时显示选区工具条里的“解释公式”。

### `POST /ai/explain-equation`  body `{paper_id, equation_id, question?, chain_id?, save?, refresh_latex?}`
SSE：`stage` → `latex`（转写结果）→ `meta` → `delta*` → `done{card}`（或 `error`）。
卡片 `object_type="equation"`，`answer` 里有 `symbols[{symbol, meaning, ords[], status: defined|undefined|unsupported}]`、`relations`、`derivation`、`equation{id,number,page,latex,confidence,warnings}`。
符号要么有论文出处（`defined`），要么明确标注“论文未定义”（`undefined`），否则标 `unsupported`（缺少依据）。符号表既认 `- 符号：含义 [P12]` / `- 符号 — 含义` 的列表，也认 `| 符号 | 含义 | 来源 |` 的 Markdown 表格；有内容却一个符号都没解析出来时，`verification.notes` 会说明“符号表的格式无法解析”。讲解这一步只发文字（LaTeX + 摘录），不再发图。

## 图表

### `GET /papers/{id}/vision/figures`
逻辑图列表（解析器切碎的子图已按图注归并）：`{items:[{id, member_ids[], page, bbox, kind: figure|table, label, caption_ord, caption, analysis_status, analysis_kind, job_id}]}`。

### `GET /papers/{id}/vision/status`
已有的公式转写与图表分析状态。

### `POST /papers/{id}/figures/{fid}/analyze`  body `{force?: bool, kind?: auto|framework|chart|table}`
创建后台任务（job `vision_figure`）→ `{job_id, status}`。已有结果且未 `force` 时 `job_id=null, status="ready"`。

### `GET /papers/{id}/figures/{fid}/analysis`
`{figure_id, status: none|pending|running|ready|failed, kind, job_id, model, error, updated_at, data, mapping, analysis, evidence[], verification, warnings[], derived_ord}`

- `kind=framework`：`data = {summary, modules[{id,label,role,kind,group,in_text}], flows[{from,to,label,kind}], groups[], uncertain[], verifiable, warnings[]}`；`mapping.modules[id] = {description, ords[], found}` 把模块对应到论文正文的描述；`analysis = {overview, flow_explanation, inferences[], limitations[]}`。
- `kind=chart`：`data = {panels[{label, chart_type, title, x_axis, y_axis, series[], observations[{text,has_unverified_numbers}], values[{text,value,precision: exact|approx, verified}]}], unreadable[], verifiable, warnings[]}`；`analysis = {direct, points[], paper_claims[{text, supported: yes|partly|no|unclear, note}], inferences[], limitations[]}`。
- `kind=table`：`data = {title, columns[], rows[][], notes, unverified_cells[[r,c]], verified_ratio, verifiable, warnings[]}`。
- `verification = {status: verified|partial|no_evidence|unverified, notes[], invalid_citations[], invalid_refs[], ungrounded_numbers[]}`。
- 表格与框架图的文字化结果会作为可检索段落写回（`passages.kind` 为 `table_data` / `figure_data`，`derived_ord` 指向它），文本里带明确的“非原文”说明，之后的问答检索可以引用；实验图（chart）不写回段落。

### `DELETE /papers/{id}/figures/{fid}/analysis` → 204
删除读图结果（同时移除派生段落）。

### `POST /papers/{id}/figures/{fid}/ask`  body `{question}`
再发送这张图的截图追问：`{answerable, answer, seen, numbers[{value, how: printed|estimated}], warnings[], model, figure_id}`。
图里看不出的内容 `answerable=false` 且 `answer` 以“图中看不出”开头；估读的数字只会是 `estimated`。
前提检查：若这张图已读过，且问题问的是“坐标/数据点/最右端”等，而目标子图是热力图、整张图是表格、或根本没有这个子图，后端**不调用模型、不发截图**，直接答“图中看不出。……”并说明原因（返回里带 `premise: "mismatch"`）；其余情况由模型在 `premise_ok` 里自查，`false` 时同样按“图中看不出”处理。
估读的数字回答里必须带“约”和估计说明：后端会在没写“约”的估读数字前补“约 ”，回答里没有任何估计说明时在末尾补一句。

## 图片
`GET /papers/{id}/equations/{eid}/image?dpi=`、`GET /papers/{id}/regions/image?page&x0&y0&x1&y1&dpi=` 返回 PNG（见 API.md）。前端需要带令牌，所以取成 Blob 再给 `<img>`（`features/figures/useBlobUrl.ts`）。

## 前端入口
- 阅读器选区工具条：选中公式区域 → “解释公式”（Σ 图标），在 AI 助手里生成公式卡片（KaTeX 渲染 + 符号表 + 对照原文截图）。
- 阅读器右侧“图表”标签：列出这篇论文的所有图和表，点开后“让视觉模型读这张图”→ 结构化结果、对照原图、追问。

## 表格批量识别（文字阅读器自动调用）

解析阶段没有单独的“表格区域”：表格主体只是图注（`Table 2: …` / `Table S4: …`）后面一串零散的短文字段落。后端从图注出发推出表格位置
（图注之后同一页上连续的“行文字”，遇到整句正文、标题、参考文献、公式或下一个图注就结束；图注在表格下面的排版会往上找；少于 2 行不算表格），
再识别成结构化的行列数据。图注必须以 `Table N:` / `Table N.` / `Table N |`（可带 `Supplementary ` / `Extended Data ` 前缀、字母编号如 `S4`）开头，
正文里的“Table 1 shows …”不会被当成图注。

- `GET /api/papers/{id}/tables-analysis` → `{total, ready, failed, running, complete, items:[{caption_ord, label, page, body_ords, bbox, status, source, error, model, data}]}`。
  只读缓存，**不调用模型**。`status`：`none | pending | running | ready | failed`；`source`：`text`（行文字自带制表符或 ` | ` 分隔、列数一致，直接建表，不调用模型）或 `vision`（视觉模型转录）；
  `body_ords` 是表格主体各行文字的段落序号（识别成功后文字界面用表格替代它们）；`data`（仅 ready）：`{title, columns, rows, notes, unverified_cells:[[行,列]…], verified_ratio, warnings}`，
  与 `GET /papers/{id}/figures/{fid}/analysis` 里表格的 `data` 同一语义：`unverified_cells` 是没能与 PDF 文字层核对的单元格，界面用虚线标出，**不得当作精确值**。
- `POST /api/papers/{id}/tables-analysis`，body `{force?: bool, ords?: number[]}` → `{job_id | null}`。创建后台任务（job type `tables_analysis`），逐张识别还没识别的表；
  `ords` 只处理这些表题段落序号的表（重试单张表），`force` 让已识别好的也重新识别。没有要识别的返回 `job_id=null`。
  没有 API Key / 没配置视觉模型时返回可读错误（`no_api_key` / `vision_not_configured`），不会留下“识别中”的残影；整体性错误（密钥无效）会中止任务，
  单张表失败只记为 `failed`（带原因）不影响其它表。被取消或整体失败时，还没出结果的行会被清掉。
- `GET /api/papers/{id}/tables/{caption_ord}/image?dpi=150` → PNG：表格区域的截图（本地渲染，不调用模型），供“对照原图”。
- 隐私：转录时发给视觉模型的表格截图记入隐私日志（`task_type=vision_table`，`includes.images=true`）并计入用量。识别结果由视觉模型转录，**不能当作原文**；
  不会写回为可检索段落。
- 数据表 `table_analysis(paper_id, caption_ord)`，随论文删除级联清除。

## 补充：读图文字里的段落编号
模型写在方括号外的段落编号（如“论文P36指出”）会在保存前换成“第 N 页”（只换真实存在的段落编号）；方括号里的 `[P36]` 引用标记保持原样，由界面渲染成可点击的引用。

### 表格核对：位置感知 + 类型一致性（`vision/table_check.py`）

只检查“这个数字在 PDF 文字层里出现过没有”不够：模型把相邻行的数填错了位置（真实例子：补充材料 Table S3 里 Digg 的 diameter 被填成同表同列“平均度”的 5.72）
也会被判成“已核对”。表格转录（图表理解里的表格、整篇批量识别共用 `figures._do_table`）现在多做两层核对，结果并进表格的 `data`：

1. **位置核对**：用表格区域文字层每个词的坐标（PyMuPDF `words`，在 PDF 全局锁内读取）把单元格与它所在的行、列对应起来。
   行标签单元格（第一列）定位到文字层里的那条文本行（y 中心相差 ≤ 3.5pt 聚成一行；行必须在上一行之后）；数值必须出现在这条文本行里，
   并且它的 x 位置要和同一列其它单元格的位置一致（各列取已定位数值 x 中心的中位数作锚点，容差取相邻列锚点间距的一半，限制在 12–60pt）。
   粗体“重复叠印”造成的 `3.343.343.34` 会还原成 `3.34`。只对纯数字单元格做位置核对；行标签在文字层里找不到的行不做位置核对（退回原来的“出现过没有”），不误报。
2. **类型一致性**（只告警、不改数值，措辞是“可能是识别错误，也可能原文如此，请对照原图”，最多 6 条）。规则故意很紧，避免把本来就混着整数和小数的列误报：
   - 行/列标签明显是计数、整数度量（diameter、number、num、count、nodes、edges、size、layers、epochs、steps、iterations、dimension、maximum/minimum degree）而单元格是小数；
     带 average / mean / ratio / rate / coefficient / length / percentage 的标签（平均值、比率、系数）不算计数；
   - 或者整行/整列有 ≥5 个数值、≥90% 是整数、只有 1 个小数，并且这个小数与表里另一处的值完全相同（疑似复制到了错行）；
   - 整行/整列有 ≥5 个数值、≥80% 带百分号，却有一个没有单位；
   - 一行里有 ≥5 个数值，同一个小数（≥2 位小数）出现了 ≥2 次。
   通用的行/列名（Value、Result、Score、Parameter、Hyperparameter、Metric、Description、Statistics…）不做整数一致性告警（超参数表的 Value 列混着整数、0.99 和带指数的写法是正常的）。

   行标签和词的比较先做 NFKC 归一化：文字层里的连字（如 `coeﬃcient` 里的 ﬃ）能和模型写的 `coefficient` 对上，否则那一行会定位不到而不做位置核对。

   注意：位置核对只回答“这个值是不是出现在文字层里它该在的位置”。如果 PDF 本身在那一格印的就是这个值（真实例子：补充材料 Table S3 里 Digg 的 diameter 在 PDF 里印的就是 5.72，
   与上一行 average degree 相同，疑似原文笔误），模型照抄是忠实的，**不会**被标为位置存疑，只有类型一致性会给一句中性的提示。

新增/变化的字段：
- `data.misplaced_cells: [[行, 列], …]`——**位置存疑**：值在文字层里有，但不在对应的行/列位置。与 `unverified_cells`（文字层里根本找不到）**互不重叠、含义不同**。旧结果没有这个字段，当作空。
- `data.verified_ratio`：位置存疑的单元格也算没核对上（`1 - (未核对 + 位置存疑) / 非空单元格数`）。
- `data.warnings`：追加一条“N 个单元格位置存疑……请对照原图”，以及类型一致性的温和告警。

界面：位置存疑的单元格用“点状下划线 + 问号”，文字层里找不到的仍是虚线下划线；表下写“N 个单元格位置存疑，请对照原图”。
回归测试：`tests/test_vision.py::test_s3_regression_misplaced_digg_diameter_is_flagged_not_verified`（用真实 PDF 文字层 + 假视觉模型返回“diameter 行 Digg=5.72”的错位结果）。
已知限制：表格没有文字层（位图）时无法做位置核对；行标签换行或跨多行的表可能定位不到（这些行不做位置核对）；列锚点至少要 2 个已定位的数值才建立。

## 图内数据表与文字层还原（表格重建）

- **解析层归类：** 落在图版面里（由“Fig. N |”图注推出，图注上方、没被别的图注隔开的所有图区域合起来）的数字堆砌、标签堆砌、子图标题段落，解析时直接归为 `figure_text`，不再进入正文阅读流（`parsing/datablocks.py`）。较长的、像句子的段落仍是正文。
- **图内数据网格：** 区内“行标签 + 至少 3 个数字”的图内文字（如 Fig.5 里每个子图下方的 Avg score 表）按纵向间距聚成带，用 PDF 文字层的词坐标重建成表（`vision/layout_tables.py`，确定性，不调用模型）：行标签重复出现几次就是并排的几块，色标刻度（没有行标签）丢掉，列名取数据行附近的文字行，子表标题取上方的子图标题。每块给重建可信度。
- **接口：** `GET /papers/{id}/tables-analysis` 的 `items` 里多了 `kind`（`table` | `figure_data`）；`figure_data` 挂在图注上（`caption_ord` 是图注段落序号），`data.blocks` 是并排的几张子表 `[{title, columns, rows, confidence}]`，`data.columns/rows` 是第一块（兼容）。`source` 新增 `layout`（文字层词坐标还原）。
- **带 “Table N:” 图注的表：** 先文字建表（行文字带分隔符），再文字层坐标还原（可信度 ≥ 0.8、至少 3 行、只有一块），都不行才用视觉模型；这两种不需要模型的路径在 `POST /papers/{id}/tables-analysis` 里当场完成，不创建后台任务。`method: "vision"` 强制用视觉模型（文字层还原得不对时换个办法再试）。
- **竞态修复：** `start()` 先写“等待中”再提交任务（之前小表格的任务可能在提交返回前就跑完，状态被覆盖回“等待中”）。
- **真实样本：** 正文 Fig.5 还原出 6 张子表（3 张 10 行 5 列、3 张 10 行 8 列，可信度 1.0）；补充材料 21 张带图注的表里 11 张可由文字层还原，其余交给文字建表/视觉模型。限制：跨页表、合并单元格/多级表头、没有文字层的位图表不走还原。

## 处理进度 `GET/POST /papers/{id}/processing`

- `overall`：`processing`（有步骤在后台运行）| `waiting`（有步骤还没开始）| `attention`（全部跑完但有失败，或因没配置而跳过）| `done`。**只有 `done` 才能说“已完成”**；公式和表格没处理好之前不会是 `done`。
- `steps`：`analysis`、`formulas`、`tables`、`index`，各有 `state`（done/running/pending/failed/skipped）、`done`/`total`、`reason`、`attention`。没配置视觉模型时公式步骤是 `skipped`，原因写“未配置视觉模型，公式仍是截图”。
- 文库列表 `GET /papers` 的每项带 `processing`（轻量版本：设置读一次、计数每类一条 SQL、不做表格检测）；单篇详情做完整的表格检测。
- `POST` 启动还没做的步骤（公式批量识别、表格还原、语义索引），幂等；导入后自动触发（受“自动分析”设置控制）。
