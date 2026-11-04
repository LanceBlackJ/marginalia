# 参考文献接口（正文引用 → 详情 / 摘要 / AI 概括）

目的：在文字阅读器里点击正文中的引用标记，查看这条参考文献的内容、摘要，或让 AI 概括它做了什么。
前端入口：`features/text/`（`RefMarker` / `RefPopover` / `RefMenu` / `RefDrawer` / `refStore`）。

## 原则

- **不编造**：解析不出的字段留空；查不到就返回 `found=false` 并说明原因；没有摘要就不概括。
- **匹配可信度诚实**：并排比较“原文参考文献”和“查到的题录”，不够相似就报 `low`，界面提示“可能不是同一篇”。
- **联网只发题录**：只把这条参考文献的标题、作者、DOI 发给 OpenAlex / Crossref / Semantic Scholar，不发论文正文。OpenAlex key（如已配置）只会发给 `api.openalex.org`。
- **AI 概括的依据只有两样**：查到的摘要，和本文里引用它的句子；发往云端的内容记入隐私日志（`includes.abstract`、`includes.citing_context`）并计入用量。

## `GET /api/papers/{id}/references`

解析出的参考文献（缓存在表里，参考文献段落变化会自动重建）。

```json
{"style": "numeric|author-year|unknown", "count": 39,
 "items": [{"index": 1, "ord": 412, "page": 7, "ords": [412, 413], "raw": "Albert, R. & Barabási, A.-L. …",
            "authors": ["Albert, R.", "Barabási, A.-L."], "title": "Statistical mechanics of complex networks",
            "year": 2002, "venue": "Rev. Mod. Phys. 74, 47", "doi": "", "arxiv": "", "url": "",
            "merged": false, "messy": false}]}
```

- `index`：数字式为论文里印的编号，作者-年份式为按出现顺序的序号；按编号排序，同一编号只保留第一条。
- `ord`：第一个对应段落的序号（可用 `jumpToOrd` 跳转）；`ords` 是这条被拆成的所有段落。
- `merged`：这条所在的段落特别宽（双栏被合并成一行），文字可能夹杂别的条目的片段；`messy`：抽出的字段不可靠（此时 `title` 为空）。
- 解析规则：行首编号（`12.`、`[12]`、`12 Author,`）定条目；没有编号的段落当作上一条的续行（上一条已以带年份的括号结尾则丢弃）；致谢/声明/出版说明等混进来的文字会被切掉；作者按“姓, 名首字母”扫描，标题到第一个句末标点（括号内的句点不算）；DOI / arXiv / 年份用正则。
- 表：`paper_references`、`reference_meta`（带哈希，用来判断缓存是否过期）、`reference_resolutions`，都带 `ON DELETE CASCADE`，随论文删除。

## `GET /api/papers/{id}/citations`

正文里能映射到参考文献的引用标记。映射不到的不返回（“Fig. 3”“Table S1”“log2”“[99]”都不会出现）。

```json
{"style": "numeric", "ref_count": 39,
 "items": [{"ord": 17, "markers": [
   {"kind": "sup", "text": "1,2", "start": 61, "end": 64, "refs": [1, 2]},
   {"kind": "bracket", "text": "[12–15]", "start": 90, "end": 97, "refs": [12, 13, 14, 15]},
   {"kind": "ref", "text": "4, 6–7", "start": 120, "end": 126, "refs": [4, 6, 7]},
   {"kind": "authoryear", "text": "Rivera and Chen, 2009", "start": 140, "end": 161, "refs": [1]}]}]}
```

- `kind`：`sup`（粘在词尾的上标数字，如 `systems1,2`）、`bracket`（`[12]`、`[12,13]`、`[12–15]`，区间展开）、`ref`（`ref. 12`、`refs 3, 4`）、`authoryear`（`(Smith et al., 2020)`、`Smith and Jones (2020)`，按第一作者姓氏 + 年份匹配，对应多条时不猜）。
- `start`/`end` 是标记在**段落原文**里的字符区间；前端显示的文字经过整理（断词、行内数学），所以前端用 `text` 在显示文字里按顺序查找，找不到就不当引用。
- 上标数字的识别要求“词本身在论文里独立出现过”（缩写要求两次），数字不能以 0 开头，数学记号（`log2`、`top5`…）和不存在的编号不算。

## `POST /api/papers/{id}/references/{index}/resolve`

联网查题录与摘要。Body：`{"force": false}`。

```json
{"index": 1, "found": true, "title": "…", "authors": ["…"], "year": 2002, "venue": "…", "doi": "…", "url": "https://doi.org/…",
 "open_access_url": "", "abstract": "…", "abstract_source": "openalex|crossref|semantic_scholar", "abstract_reason": "",
 "citations": 12, "source": "openalex|crossref|semantic_scholar",
 "match": {"score": 0.97, "level": "high|medium|low", "reason": "标题相似度 0.97，第一作者一致"},
 "in_library": {"paper_id": "…", "title": "…"} , "cached": false, "network": "OpenAlex / Crossref / Semantic Scholar"}
```

- 查询顺序：有 DOI（或 arXiv 号，用 `10.48550/arxiv.<号>`）先精确查；否则用标题（没有标题用整条原文）在 OpenAlex（按年份 ±1）和 Crossref 里做书目查询；缺摘要时用同一篇的其它候选，再用 Semantic Scholar 补充。
- `match.level`：`high` ≥ 0.9，`medium` ≥ 0.75，其余 `low`；DOI 完全一致直接 `high`。第一作者不一致 −0.15、年份差超过 1 −0.1，一致分别加分。
- `found=false` 时返回 `reason`，没有 `abstract`。有题录没摘要时 `abstract` 为空并给出 `abstract_reason`。
- **摘要一致性检查**：OpenAlex 偶尔把别的论文的摘要挂在一条记录上（标题匹配 1.00，摘要却是另一篇），所以每份摘要在使用前都和标题做词汇层面的比较（`abstract_consistency`）：标题实词去停用词、词干化后，看加权覆盖率，并单独看“不泛的核心词”（`game`、`human`、`knowledge`、`network` 这类泛词不算）；短词（≤3 字母，如 `go`）必须整词出现，不会被 `gomoku` 命中；中文标题用字符二元组；标题和摘要语言不同（无法比较）按可信处理。`mismatch`：覆盖率 < 0.30，或核心词 ≥ 2 个而一个都没出现，或只有 1 个核心词没出现且覆盖率 < 0.5；`weak`：覆盖率 < 0.5（仍使用，但带提示）。
- **不可信就换来源**：先看匹配记录自己的摘要，再看同一篇的其它候选，最后向 Semantic Scholar（DOI，其次标题）和 Crossref（DOI）要。换了来源会在 `abstract_notes` 里写明“OpenAlex 的摘要与这篇的标题不相符，已忽略，改用 Semantic Scholar 的摘要”。所有来源都不可信（或都没有）时：仍然 `found=true`，但 `abstract=null`，`abstract_mismatch=true`，`abstract_reason="检索到的摘要与这篇的标题不相符，已忽略；请去原文查看。"`，被忽略的错误摘要不会出现在返回里。结果里带 `v`（版本），旧版本的缓存会被忽略并重新查询。
- 这只是词汇层面的检查，不是语义判断：标题很短、或错误摘要恰好包含标题的核心词时可能漏掉；同一篇有多个来源的摘要时靠“换来源”兜底。
- `in_library`：按 DOI 或归一化标题判断是否已在文库。结果缓存，`force=true` 才重新联网。

## `GET /api/papers/{id}/references/{index}/contexts`

本文里引用这条参考文献的句子：`{"items": [{"ord", "page", "sentence"}]}`（每个段落取包含标记的那一句，最多 8 条）。

## `POST /api/ai/summarize-reference`（SSE）

Body：`{"paper_id": "…", "index": 3}`。事件：`meta`（`model`、`match`、`notes`、`basis`）→ `stage` / `delta`… → `done`；失败为 `error`。

- **没有摘要，或没查到**：不调用模型，直接 `done`：`{"summary": null, "reason": "no_abstract|not_found", "message": "没有可用摘要，无法概括。可以去查看原文。", "link": "…"}`。
- **摘要不可信（被忽略）与没有摘要走同一条路径**：不调用模型，`done` 里 `summary=null`、`reason="no_abstract"`，`message` 带原因（如“检索到的摘要与这篇的标题不相符，已忽略”），并给出原文链接。
- **有摘要**：用快速模型，只依据摘要和本文引用句，输出“这篇参考文献做了什么”（每句标注“（摘要）”）和“与本文的关系”（标注“（本文第 N 页）”；引用处没说明用途就写“引用处没有说明用途”）。`done` 里带 `label`（“AI 概括，依据：摘要 + 本文引用处”）、`basis`（摘要来源与引用处序号/页码）、`notes`。
- `match.level == "low"` 时 `notes` 里带“可能不是同一篇”的提示。强模型暂时不可用的回退规则与其它 AI 接口一致。
- 隐私日志：`task_type=summarize`，`includes.abstract=true`，有引用句时 `includes.citing_context=true`。

## 前端交互

- 正文里**后端确认过**的引用标记渲染成可点击的小标记（上标数字、`[12]`、`refs 3`、作者-年份都支持；逗号分开的每个编号各自可点；区间 `14–18` 是一个整体，浮层里可切换）。
- **点击**：贴近标记的浮层——原文、解析出的题录、本文引用处（可跳转），按钮“查看摘要 / AI 概括 / 更多”。
- **右键**（或键盘菜单键、Shift+F10）：查看详情、读摘要、让 AI 概括、在浏览器打开 DOI/链接、加入文库（只存元数据，用 `/import/doi` 的 `metadata_only`）/ 已在库则打开、复制引文、复制 BibTeX。
- **详情抽屉**：原文、题录、匹配可信度（低时醒目提示）、摘要全文与来源、AI 概括（流式，标“AI 生成”和依据）、本文引用处、复制/打开。
- 键盘：Tab 聚焦标记，Enter 打开浮层，Esc 关闭；菜单里上下键移动。正文末尾“参考文献”列表里的每一条同样可点击、可右键。
- 查询时界面写明“将联网查询 OpenAlex / Crossref，只发送这条参考文献的标题、作者或 DOI，不发送论文正文”。

## 已知限制

- 双栏论文的参考文献段落可能被交错、合并：整理不干净的条目标 `merged` / `messy`，界面提示“请以原文为准”，不报告标题。
- 解析只覆盖编号式与“姓, 名首字母 …（年份）”式；其它写法返回 `unknown` 风格，标记识别只处理明确写法。
- 书籍、网页、预印本、未被收录的会议论文可能查不到；查到的题录与摘要来自 OpenAlex / Crossref / Semantic Scholar，不是原文，摘要可能缺失。
- 行内数学、PDF 视图里的引用编号没有接入（只做了文字阅读器）。
